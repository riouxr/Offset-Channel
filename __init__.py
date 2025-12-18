bl_info = {
    "name": "Universal Animation Offset (Stable + TX RX SX)",
    "author": "Robert Rioux + ChatGPT",
    "version": (6, 2),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Animation Tab",
    "description": "Offsets animation channels live using TX/RX/SX labels. Stable + slotted actions + constraints + shapekeys.",
    "category": "Animation",
}

import bpy

# ============================================================
# Friendly naming TX/RX/SX etc.
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
# Slotted Actions helpers (Blender 4.4+)
# ============================================================

def iter_fcurves_from_animdata(animdata):
    """
    Yield F-Curves for this datablock's AnimData.
    Supports:
      - legacy: action.fcurves
      - slotted actions: action.layers[*].strips[*].channelbags[*].fcurves filtered by animdata.action_slot_handle
    """
    if not animdata:
        return
    action = getattr(animdata, "action", None)
    if not action:
        return

    # --- Legacy API path (works when action.fcurves is available & populated)
    fcurves = getattr(action, "fcurves", None)
    if fcurves is not None:
        # In slotted actions, this legacy collection may exist but be empty/wrong-slot.
        # We only trust it if it actually contains curves.
        try:
            if len(fcurves) > 0:
                for fc in fcurves:
                    yield fc
                return
        except Exception:
            # Fall back to slotted path
            pass

    # --- Slotted Actions path
    handle = getattr(animdata, "action_slot_handle", 0)

    layers = getattr(action, "layers", None)
    if not layers:
        return

    for layer in action.layers:
        strips = getattr(layer, "strips", None)
        if not strips:
            continue
        for strip in layer.strips:
            channelbags = getattr(strip, "channelbags", None)
            if not channelbags:
                continue
            for cb in strip.channelbags:
                cb_handle = getattr(cb, "slot_handle", 0)
                if cb_handle != handle:
                    continue
                cb_fcurves = getattr(cb, "fcurves", None)
                if not cb_fcurves:
                    continue
                for fc in cb_fcurves:
                    yield fc


def _iter_matching_fcurves(animdata, data_path, index):
    for fc in iter_fcurves_from_animdata(animdata):
        if fc.data_path == data_path and fc.array_index == index:
            yield fc


# ============================================================
# Channel scanning
# ============================================================

def gather_channels(context):
    out = []

    for obj in context.selected_objects:

        # ============================
        # Object / Bone / Constraint
        # ============================
        anim = obj.animation_data
        if anim and anim.action:
            for f in iter_fcurves_from_animdata(anim):
                path = f.data_path
                idx = f.array_index

                # Skip bones in Object Mode
                if context.mode == 'OBJECT' and 'pose.bones' in path:
                    continue

                bone_name = None
                bone_end = -1
                bone_selected = True

                if 'pose.bones' in path:
                    start = path.find('pose.bones["') + 12
                    end = path.find('"]', start)
                    if end > start:
                        bone_name = path[start:end]
                        bone_end = end
                        if context.mode == 'POSE' and obj.type == 'ARMATURE':
                            try:
                                bone_selected = obj.pose.bones[bone_name].bone.select
                            except KeyError:
                                bone_selected = False

                if not bone_selected:
                    continue

                # Custom properties
                if bone_name:
                    rem = path[bone_end + 2:]
                    if rem.startswith('["'):
                        prop = rem[2:rem.find('"]')]
                        out.append((f"{obj.name} | {bone_name} | {prop} [{idx}]", path, idx, obj.name))
                        continue
                else:
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

        # ============================
        # Shape keys (Key datablock)
        # ============================
        if obj.type == 'MESH' and obj.data.shape_keys:
            sk_anim = obj.data.shape_keys.animation_data
            if sk_anim and sk_anim.action:
                for f in iter_fcurves_from_animdata(sk_anim):
                    path = f.data_path
                    if path.startswith('key_blocks["'):
                        name = path[path.find('["') + 2:path.find('"]')]
                        out.append((f"{obj.name} | SHAPEKEY | {name}", path, f.array_index, obj.name))

    # Deduplicate
    uniq = []
    for c in out:
        if c not in uniq:
            uniq.append(c)
    return uniq


# ============================================================
# Slider update → apply delta
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
# Live keyframe offsetting
# ============================================================

def apply_live_offset(context, item):
    delta = item.value - item.prev
    if delta == 0.0:
        return

    item.prev = item.value

    obj = context.scene.objects.get(item.obj_name)
    if not obj:
        return

    # Shape key channels live on Key datablock AnimData
    if item.data_path.startswith('key_blocks["'):
        if obj.type != 'MESH' or not obj.data.shape_keys:
            return
        sk_anim = obj.data.shape_keys.animation_data
        if not sk_anim or not getattr(sk_anim, "action", None):
            return
        matches = list(_iter_matching_fcurves(sk_anim, item.data_path, item.index))
    else:
        anim = obj.animation_data
        if not anim or not getattr(anim, "action", None):
            return
        matches = list(_iter_matching_fcurves(anim, item.data_path, item.index))

    if not matches:
        return

    for f in matches:
        for k in f.keyframe_points:
            k.co[1] += delta
        f.update()

    # Redraw views
    for area in context.screen.areas:
        if area.type in {"VIEW_3D", "GRAPH_EDITOR"}:
            area.tag_redraw()


# ============================================================
# Refresh channel logic
# ============================================================

def refresh_channels(context):
    props = context.scene.anim_offset
    props.channels.clear()

    found = gather_channels(context)
    for label, path, idx, obj_name in found:
        it = props.channels.add()
        it.label = label
        it.data_path = path
        it.index = idx
        it.obj_name = obj_name
        it.value = 0.0
        it.prev = 0.0

    props.channel_count = len(props.channels)


# ============================================================
# Timer for selection updates
# ============================================================

def poll_selection_timer():
    context = bpy.context
    if not context or not context.scene:
        return 0.2
    props = context.scene.anim_offset

    sel_objects = sorted([o.name for o in context.selected_objects])
    hash_str = ",".join(sel_objects)

    if context.mode == 'POSE':
        sel_bones = []
        for o in context.selected_objects:
            if o.type == 'ARMATURE':
                sel_bones.extend([b.name for b in o.pose.bones if b.bone.select])
        sel_bones.sort()
        hash_str += ";" + ",".join(sel_bones)

    if props.last_selection_hash != hash_str:
        props.last_selection_hash = hash_str
        refresh_channels(context)

    return 0.2


# ============================================================
# Panel
# ============================================================

class ANIM_PT_offset_panel(bpy.types.Panel):
    bl_label = "Universal Offset Channels"
    bl_idname = "ANIM_PT_universal_offset"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Animation"

    def draw(self, context):
        layout = self.layout
        props = context.scene.anim_offset

        if props.channel_count == 0:
            layout.label(text="No animated channels found in selection.")
            return

        layout.separator()

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
    bpy.app.timers.register(poll_selection_timer, first_interval=0.1)


def unregister():
    if bpy.app.timers.is_registered(poll_selection_timer):
        bpy.app.timers.unregister(poll_selection_timer)
    del bpy.types.Scene.anim_offset
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()