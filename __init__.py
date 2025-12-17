bl_info = {
    "name": "Universal Animation Offset (Stable + TX RX SX)",
    "author": "Robert Rioux + ChatGPT",
    "version": (6, 5),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Animation Tab",
    "description": "Offsets animation channels live using TX RX SX labels. Supports bones, constraints, shapekeys, slotted actions.",
    "category": "Animation",
}

import bpy
from bpy.app.handlers import persistent

# ============================================================
# Blender 4.5 / 5.0 pose bone selection compatibility
# ============================================================

def posebone_is_selected(pb):
    # Blender 5.0+
    if hasattr(pb, "select"):
        return pb.select
    # Blender ≤4.5
    if hasattr(pb.bone, "select"):
        return pb.bone.select
    return False

# ============================================================
# F-Curve iterator (legacy + layered, bone-safe)
# ============================================================

def iter_fcurves_from_animdata(animdata):
    if not animdata:
        return

    action = getattr(animdata, "action", None)
    if not action:
        return

    seen = set()

    # --- Legacy storage (bones frequently live here)
    fcurves = getattr(action, "fcurves", None)
    if fcurves:
        for fc in fcurves:
            key = (fc.data_path, fc.array_index)
            if key not in seen:
                seen.add(key)
                yield fc

    # --- Layered / slotted storage (NLA / Action Layers)
    layers = getattr(action, "layers", None)
    if not layers:
        return

    slot = getattr(animdata, "action_slot", None)
    if slot is None:
        slots = getattr(action, "slots", None)
        if slots:
            slot = slots[0]

    for layer in layers:
        for strip in getattr(layer, "strips", []):
            for cb in getattr(strip, "channelbags", []):
                cb_fcurves = getattr(cb, "fcurves", None)
                if not cb_fcurves:
                    continue

                cb_slot = getattr(cb, "slot", None)

                # Slot filtering EXCEPT for pose bones
                if slot and cb_slot and cb_slot != slot:
                    if not any(fc.data_path.startswith('pose.bones["') for fc in cb_fcurves):
                        continue

                for fc in cb_fcurves:
                    key = (fc.data_path, fc.array_index)
                    if key not in seen:
                        seen.add(key)
                        yield fc


def _iter_matching_fcurves(animdata, data_path, index):
    for fc in iter_fcurves_from_animdata(animdata):
        if fc.data_path == data_path and fc.array_index == index:
            yield fc

# ============================================================
# UI Properties
# ============================================================

def slider_update(self, context):
    apply_live_offset(context, self)


class AnimChannelItem(bpy.types.PropertyGroup):
    label: bpy.props.StringProperty()
    data_path: bpy.props.StringProperty()
    index: bpy.props.IntProperty()
    obj_name: bpy.props.StringProperty()
    prev: bpy.props.FloatProperty(default=0.0)
    value: bpy.props.FloatProperty(default=0.0, update=slider_update)


class AnimOffsetProperties(bpy.types.PropertyGroup):
    channels: bpy.props.CollectionProperty(type=AnimChannelItem)
    channel_count: bpy.props.IntProperty(default=0)
    last_selection_hash: bpy.props.StringProperty(default="")

# ============================================================
# Label helpers
# ============================================================

def short_name(path, idx):
    if "location" in path:
        return ["TX", "TY", "TZ"][idx]
    if "rotation_euler" in path:
        return ["RX", "RY", "RZ"][idx]
    if "rotation_quaternion" in path:
        return ["RW", "RX", "RY", "RZ"][idx]
    if "rotation_axis_angle" in path:
        return ["RW", "RX", "RY", "RZ"][idx]
    if "scale" in path:
        return ["SX", "SY", "SZ"][idx]
    return f"[{idx}]"

# ============================================================
# Channel scanning
# ============================================================

def gather_channels(context):
    out = []

    for obj in context.selected_objects:
        anim = obj.animation_data
        if anim and anim.action:
            for f in iter_fcurves_from_animdata(anim):
                path = f.data_path
                idx = f.array_index

                # Skip bones in Object Mode
                if context.mode == 'OBJECT' and 'pose.bones' in path:
                    continue

                bone_name = None
                bone_selected = True

                if 'pose.bones["' in path:
                    start = path.find('pose.bones["') + 12
                    end = path.find('"]', start)
                    if end > start:
                        bone_name = path[start:end]
                        if context.mode == 'POSE' and obj.type == 'ARMATURE':
                            pb = obj.pose.bones.get(bone_name)
                            bone_selected = bool(pb and posebone_is_selected(pb))

                if not bone_selected:
                    continue

                # Custom properties
                if path.startswith('["'):
                    prop = path[2:path.find('"]')]
                    out.append((f"{obj.name} | {prop} [{idx}]", path, idx, obj.name))
                    continue

                # Constraints
                if path.startswith("constraints["):
                    cname = path[path.find('["') + 2:path.find('"]')]
                    prop = path[path.find('"]') + 2:]
                    out.append((f"{obj.name} | CONSTRAINT | {cname} | {prop} [{idx}]", path, idx, obj.name))
                    continue

                # Transforms
                if any(k in path for k in ("location", "rotation", "scale")):
                    if bone_name:
                        label = f"{obj.name} | {bone_name} | {short_name(path, idx)}"
                    else:
                        label = f"{obj.name} | {short_name(path, idx)}"
                    out.append((label, path, idx, obj.name))

        # Shape keys
        if obj.type == 'MESH' and obj.data.shape_keys:
            sk_anim = obj.data.shape_keys.animation_data
            if sk_anim and sk_anim.action:
                for f in iter_fcurves_from_animdata(sk_anim):
                    path = f.data_path
                    if path.startswith('key_blocks["'):
                        name = path[path.find('["') + 2:path.find('"]')]
                        out.append((f"{obj.name} | SHAPEKEY | {name}", path, f.array_index, obj.name))

    # Deduplicate while preserving order
    return list(dict.fromkeys(out))

# ============================================================
# Live offset application
# ============================================================

def apply_live_offset(context, item):
    delta = item.value - item.prev
    if delta == 0.0:
        return

    item.prev = item.value
    obj = context.scene.objects.get(item.obj_name)
    if not obj:
        return

    if item.data_path.startswith('key_blocks["'):
        sk = obj.data.shape_keys
        if not sk or not sk.animation_data:
            return
        animdata = sk.animation_data
    else:
        animdata = obj.animation_data

    if not animdata or not animdata.action:
        return

    for f in _iter_matching_fcurves(animdata, item.data_path, item.index):
        for k in f.keyframe_points:
            k.co[1] += delta
        f.update()

    for area in context.screen.areas:
        if area.type in {"VIEW_3D", "GRAPH_EDITOR"}:
            area.tag_redraw()

# ============================================================
# Refresh logic
# ============================================================

def refresh_channels(context):
    props = context.scene.anim_offset
    props.channels.clear()

    for label, path, idx, obj_name in gather_channels(context):
        it = props.channels.add()
        it.label = label
        it.data_path = path
        it.index = idx
        it.obj_name = obj_name
        it.prev = 0.0
        it.value = 0.0

    props.channel_count = len(props.channels)

# ============================================================
# Selection handler
# ============================================================

@persistent
def selection_update_handler(scene, depsgraph):
    context = bpy.context
    if not context or not context.scene or not hasattr(context.scene, "anim_offset"):
        return

    props = context.scene.anim_offset

    sel_objects = sorted(o.name for o in context.selected_objects)
    hash_str = ",".join(sel_objects)

    if context.mode == 'POSE':
        sel_bones = []
        for o in context.selected_objects:
            if o.type == 'ARMATURE':
                sel_bones.extend(
                    b.name for b in o.pose.bones if posebone_is_selected(b)
                )
        sel_bones.sort()
        hash_str += ";" + ",".join(sel_bones)

    if props.last_selection_hash != hash_str:
        props.last_selection_hash = hash_str
        refresh_channels(context)

# ============================================================
# Panel
# ============================================================

class ANIM_PT_offset_panel(bpy.types.Panel):
    bl_label = "BB Offset Channels"
    bl_idname = "ANIM_PT_universal_offset"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Animation"

    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_offset

        if not props.channels:
            layout.label(text="No animated channels found.")
            return

        for ch in props.channels:
            layout.prop(ch, "value", text=ch.label)

# ============================================================
# Register
# ============================================================

classes = (
    AnimChannelItem,
    AnimOffsetProperties,
    ANIM_PT_offset_panel,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.anim_offset = bpy.props.PointerProperty(type=AnimOffsetProperties)
    bpy.app.handlers.depsgraph_update_post.append(selection_update_handler)
    bpy.app.timers.register(lambda: refresh_channels(bpy.context), first_interval=0.1)

def unregister():
    if selection_update_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(selection_update_handler)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.anim_offset

if __name__ == "__main__":
    register()
