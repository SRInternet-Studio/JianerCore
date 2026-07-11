from abc import ABC

from ..utils.hypetyping import OneBotSegReg

message_types = {}


class SegmentBase(ABC):
    def __init__(self, *args, **kwargs):
        annotations = self.__anns
        values = {}

        for name, value in zip(annotations, args):
            values[name] = value

        for name, value in kwargs.items():
            try:
                values[name] = annotations[name](value)
            except TypeError:
                values[name] = value

        for name, annotation in annotations.items():
            if name in values:
                continue
            default = self.__var.get(name)
            values[name] = None if default is None else annotation(default)

        for name, value in values.items():
            setattr(self, name, value)

    def __init_subclass__(cls, **kwargs):
        segment_type = kwargs.get("sg_type") or kwargs.get("st")
        summary_template = kwargs.get("summary_tmp") or kwargs.get("su")
        if segment_type is None and summary_template is None:
            return

        cls.__sg_type = segment_type
        cls.__var = dict(vars(cls))
        cls.__anns = cls.__var.get("__annotations__", {})

        def to_str(self) -> str:
            text = "[]" if summary_template is None else summary_template
            for name in cls.__anns:
                text = text.replace(f"<{name}>", str(getattr(self, name, None)))
            return text

        if cls.__str__ is SegmentBase.__str__:
            cls.__str__ = to_str

        message_types[segment_type]: OneBotSegReg = {
            "type": cls,
            "args": list(cls.__anns),
        }

    def to_json(self) -> dict:
        data = {}
        for name, annotation in self.__anns.items():
            value = getattr(self, name)
            if value is not None:
                data[name] = value if isinstance(value, annotation) else annotation(value)
        return {"type": self.__sg_type, "data": data}

    def __str__(self) -> str: return "__not_set__"

    def __eq__(self, other) -> bool:
        return type(self) is type(other) and self.to_json() == other.to_json()

    def __ne__(self, other) -> bool:
        return not self == other
