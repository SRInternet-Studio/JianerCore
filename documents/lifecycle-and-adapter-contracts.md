# JianerCore 0.92 插件生命周期与适配器契约

本文记录 JianerCore 0.92 的宿主接线方式，以及插件可以依赖的跨适配器公共类型。
这些接口主要用于需要观察消息、参与宿主 fallback、解析引用或安全读取媒体的插件。

## 迁移摘要

0.92 对公开 ID 边界做了统一：

- `ExternalId = str`。
- 事件中的 `self_id`、`user_id`、`group_id`、`conversation_id`、`message_id`
  以及 notice/request 事件中的账号、消息和操作者 ID 均为字符串。
- OneBot、Milky 等要求数值 ID 的协议只在适配器发包边界转换为整数。
- 业务代码不应再对事件 ID 执行算术，也不应使用 `isinstance(id, int)` 判断协议。

`Client.subscribe()` 现在返回精确的 `SubscriptionToken`。调用
`client.unsubscribe(token)` 可幂等移除这一条订阅；第二次移除返回 `False`。

同步代码可使用 `client.close()` / `client.restart()`。异步处理函数应优先使用：

```python
await client.aclose()
await client.arestart()
```

`Client` 的同步和异步上下文退出、正常运行结束及重启都会关闭当前插件
Manager。插件应把线程、任务、连接和临时文件的清理放进 `shutdown()`。

## 消息派发顺序

Manager 提供三个独立阶段：

```python
await manager.observe(event, actions)
handled = await manager.dispatch(event, actions, run_observers=False)
if not handled:
    handled = await manager.dispatch_fallback(event, actions)
```

插件分别暴露以下 hook：

```python
async def on_message_observe(event, actions):
    # 观察消息；返回值不会截断后续处理
    ...


async def on_message(event, actions):
    # 返回严格的 True 表示已处理
    return False


async def on_message_fallback(event, actions):
    # 普通插件和宿主命令均未处理后执行
    return False
```

为兼容直接派发，`manager.dispatch(event, actions)` 默认
`run_observers=True`。宿主若已经显式调用 `observe()`，必须传
`run_observers=False`，避免同一消息被观察两次。

## setup 与 shutdown

插件可以提供同步 `setup()` 和同步或异步 `shutdown()`：

```python
def setup(client, manager):
    token = client.subscribe(handle_notice, NoticeEvent)


async def shutdown(client, manager):
    await service.close()
```

`setup()` 必须是同步函数。Manager 会记录 `setup()` 作用域内创建的订阅，
在 staged Manager 激活前保持禁用，并在关闭时精确移除。

`shutdown()` 按依赖顺序的逆序执行。它应当：

- 可以在资源尚未完全启动时安全执行；
- 自身幂等，或只依赖 Manager 提供的一次性 shutdown 保障；
- 停止并等待后台任务、线程和连接；
- 不清理其他插件或其他 Manager 的全局状态。

如果加载或 staged setup 失败，宿主必须对失败的 Manager 调用
`await manager.shutdown()`。这会回滚已成功 setup 的插件、订阅、Alconna
matcher 和该代插件目录下由真实 `__file__` 确认归属的模块；共享依赖不会
因为名称相似而被删除。

## 两阶段运行时重载

首次同步加载可以继续使用：

```python
result = client.load_plugins("plugins")
if result.failed:
    raise RuntimeError(result.failed)
```

运行中的真实重载应使用锁，并先在旧 Manager 仍服务时构建 staged Manager：

```python
from jianer.plugins import PluginManager, PluginSetupError


async def reload_plugins(client, current_manager, reload_lock):
    async with reload_lock:
        candidate = PluginManager()
        result = candidate.load_plugins("plugins")
        if result.failed:
            await candidate.shutdown()
            return current_manager, result

        try:
            candidate.setup_client(client, activate=False)
        except PluginSetupError:
            await candidate.shutdown()
            return current_manager, result

        old_manager = client.swap_plugin_manager(
            candidate,
            expected=current_manager,
        )
        if old_manager is not None:
            report = await old_manager.shutdown()
            if not report.completed:
                # Manager 仍处于 draining；资源释放后可以再次调用 shutdown。
                ...
        return candidate, result
```

`swap_plugin_manager()` 只接受已绑定到同一 `Client` 的 staged Manager。
如果当前 Manager 已被其他重载替换，或新 Manager 不能激活，交换失败并保留
旧 Manager。交换后旧 Manager 不再接收新派发，但已取得 lease 的 in-flight
回调会先完成，再执行逆序 shutdown。

不要在运行时重载中调用 Alconna 的全局 `_clear_matchers()`，也不要按入口随机
模块名清理 `sys.modules`。Matcher、订阅和模块均由各 Manager/插件代拥有。

## 会话键

跨协议会话使用不可变 `ConversationKey`：

```python
from jianer.adapters import ConversationKey

key = ConversationKey.from_event(event, preset="default")
```

其稳定字段为：

```text
(protocol, self_id, kind, conversation_id, preset)
```

这意味着同一协议下的不同机器人账号不会共享会话；群聊和私聊不会碰撞；同一
会话的不同 preset 也天然隔离。

## 能力声明

`actions.capabilities` 是 `frozenset[Capability]`。插件必须先检查能力，不应
用协议名称猜测接口是否可用。

| 能力 | OneBot | Milky | Feishu | Kritor |
| --- | --- | --- | --- | --- |
| `RESOLVE_REFERENCE` | 是 | 是 | 是 | 否 |
| `RESOLVE_MEDIA` | 是 | 是 | 否 | 否 |
| `SEND_REPLY` | 是 | 是 | 是 | 是 |
| `SEND_IMAGE` | 是 | 是 | 是 | 以实际实现为准 |
| `SEND_AUDIO` | 是 | 是 | 是 | 以实际实现为准 |
| `NATIVE_GROUP_FORWARD` | 是 | 是 | 否 | 否 |
| `RESOLVE_FORWARD` | 是 | 是 | 否 | 否 |

飞书的长文本降级为普通文本发送，不属于原生群转发。插件在能力缺失时应退化到
文本或跳过附件，不应伪造成功。

## 引用解析

引用和媒体是两个能力。引用解析不会自动下载引用中的文件：

```python
result = await actions.resolve_reference(
    event.message_id,
    conversation=key,
)
```

`ReferenceResolution` 固定包含：

- `status` 与 `error_code`
- `message_id`
- `conversation`
- `sender_id`
- `sent_at`
- `segments`

成功结果会验证引用仍属于请求的会话，避免通过消息 ID 读取其他群或私聊内容。
失败通过 `ResolutionStatus` / `ResolutionErrorCode` 表达，不把上游异常对象或
凭据写进结果。

## 媒体解析与安全策略

媒体解析返回固定 `MediaResolution`：

```python
result = await actions.resolve_media(
    request,
    conversation=key,
    policy=MediaPolicy(
        max_bytes=10 * 1024 * 1024,
        total_timeout_seconds=15,
        allowed_remote_origins=frozenset({
            "https://media.example.com",
        }),
    ),
)
```

结果字段为 `status/error_code/mime/size/data/source`。`source` 是脱敏标签，
不会包含 URL 查询参数、data URI 内容、本地完整路径或适配器资源 token。

安全默认值如下：

- 远程 URL 只允许 `http` / `https`，禁止 URL 用户名和密码。
- `allowed_remote_origins` 默认为空；每次重定向都会重新校验 origin 和 DNS。
- 普通远程 origin 解析到 loopback、私网、link-local 等非公网地址时拒绝。
- `ADAPTER_RESOURCE` 是独立来源类型；当前适配器若没有实现，会明确返回
  `UNSUPPORTED`，不会把它当成可绕过公网检查的普通 URL。
- 限制重定向次数、连接超时、总超时、声明长度和实际流式读取字节数。
- 根据文件签名嗅探 MIME，并同时校验 `MediaKind` 和显式允许的 MIME。
- data URI 必须是严格 Base64，并在解码前后检查大小。
- 本地文件默认拒绝；只有解析后的真实路径位于 `allowed_local_roots` 下才允许，
  且拒绝 UNC、设备路径和远程 `file://host/...`。

不要把用户消息中的 URL 直接交给模型 SDK，也不要把 HTTPS 降级为 HTTP。先让
适配器解析为受限字节，再把 `data` 与经过验证的 `mime` 传给模型。

`SEND_IMAGE` / `SEND_AUDIO` 表示适配器具备出站传输能力，不表示任意 locator
已经通过上述入站解析策略。插件只能直接发送自己生成或宿主明确信任的资源；
用户提供的 URL、文件路径或资源 token 必须先经过 `resolve_media()`。飞书当前
不提供 `RESOLVE_MEDIA`，因此其用户提供的 locator 应降级为文本，不能直接交给
出站上传接口。
