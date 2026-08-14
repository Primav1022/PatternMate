"""Narrow compatibility shim for AutoDL's newest mirrored comfy-kitchen wheel."""

try:
    import comfy_kitchen

    if not hasattr(comfy_kitchen, "int8_attention_is_available"):
        comfy_kitchen.int8_attention_is_available = lambda: False
except ImportError:
    pass
