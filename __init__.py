bl_info = {
    "name": "Quaternion Bone Animation Offset",
    "author": "Robert Rioux",
    "version": (1, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Animation Tab",
    "description": "Offsets quaternion bone animation curves for selected bones in Pose Mode",
    "category": "Animation",
}

import bpy

class BoneAnimOffsetProperties(bpy.types.PropertyGroup):
    offset_w: bpy.props.FloatProperty(name="W", default=0.0, update=lambda s,c: update_offsets(c))
    offset_x: bpy.props.FloatProperty(name="X", default=0.0, update=lambda s,c: update_offsets(c))
    offset_y: bpy.props.FloatProperty(name="Y", default=0.0, update=lambda s,c: update_offsets(c))
    offset_z: bpy.props.FloatProperty(name="Z", default=0.0, update=lambda s,c: update_offsets(c))
    prev_values: bpy.props.FloatVectorProperty(size=4, default=(0.0, 0.0, 0.0, 0.0))

def update_offsets(context):
    props = context.scene.bone_anim_offset
    obj = context.object
    if not obj or obj.type != 'ARMATURE' or not obj.animation_data or not obj.animation_data.action:
        return

    action = obj.animation_data.action

    # Compute delta relative to last slider values
    current = [props.offset_w, props.offset_x, props.offset_y, props.offset_z]
    delta = [c - p for c, p in zip(current, props.prev_values)]
    props.prev_values = current.copy()

    selected_bones = [pb.name for pb in context.selected_pose_bones]

    # Offset F-Curves
    for fcurve in action.fcurves:
        for bone_name in selected_bones:
            if f'pose.bones["{bone_name}"].rotation_quaternion' in fcurve.data_path:
                idx = fcurve.array_index
                off = delta[idx]
                if off != 0:
                    for key in fcurve.keyframe_points:
                        key.co[1] += off
                    fcurve.update()

    # Force depsgraph + redraw so bones update
    obj.update_tag(refresh={'DATA'})
    context.view_layer.update()
    for area in context.screen.areas:
        if area.type in {'VIEW_3D', 'GRAPH_EDITOR'}:
            area.tag_redraw()

class ANIM_PT_BoneAnimOffsetPanel(bpy.types.Panel):
    bl_label = "Bone Animation Offset"
    bl_idname = "ANIM_PT_bone_anim_offset"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Animation'

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'

    def draw(self, context):
        layout = self.layout
        props = context.scene.bone_anim_offset
        layout.prop(props, "offset_w")
        layout.prop(props, "offset_x")
        layout.prop(props, "offset_y")
        layout.prop(props, "offset_z")

classes = (
    BoneAnimOffsetProperties,
    ANIM_PT_BoneAnimOffsetPanel,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.bone_anim_offset = bpy.props.PointerProperty(type=BoneAnimOffsetProperties)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.bone_anim_offset

if __name__ == "__main__":
    register()
