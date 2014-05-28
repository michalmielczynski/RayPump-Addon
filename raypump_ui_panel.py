bl_info = {
    'name': 'RayPump Online Accelerator',
    'author': 'michal.mielczynski@gmail.com, tiago.shibata@gmail.com',
    'version': '(1, 1, 0, 0)',
    'blender': (2, 7, 0),
    'location': 'Properties > Render > RayPump.com',
    'description': 'Easy to use free online GPU-farm for Cycles',
    'category': 'Render'
    }

import bpy
import socket
import json
import os
import os.path

from bpy.props import *

TCP_IP = '127.0.0.1'
TCP_PORT = 5005
SOCKET = None
RAYPUMP_PATH = None
RAYPUMP_VERSION = 1.100    # what version we will connect to?


class MessageViewOperator(bpy.types.Operator):
    bl_idname = "object.raypump_view_operator"
    bl_label = "View"
    bl_description = "Opens folder containing last render(s)"

    def execute(self, context):
        global SOCKET, TCP_IP, TCP_PORT, RAYPUMP_PATH

        if SOCKET is None:
            self.report({'WARNING'}, "Not connected")
            return {'CANCELLED'}

        try:
            the_dump = json.dumps({'VIEW': 'LAST_RENDER'})
            SOCKET.sendall(bytes(the_dump, 'UTF-8'))

        except socket.error as msg:
            self.report({'ERROR'}, "Error connecting RayPump client")
            SOCKET = None
            print(msg)
            return {'CANCELLED'}

        return {'FINISHED'}


class MessageRenderOperator(bpy.types.Operator):
    bl_idname = "object.raypump_message_operator"
    bl_label = "Render Online"
    bl_description = "Sends current scene to the RayPump Accelerator"

    def connect(self, context):
        global SOCKET, TCP_IP, TCP_PORT, RAYPUMP_PATH

        if (SOCKET is not None):
            SOCKET.close()
        try:
            SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except socket.error as msg:
            self.report({'ERROR'}, "Cannot create a socket")
            SOCKET = None
            return False

        try:
            SOCKET.connect((TCP_IP, TCP_PORT))
        except socket.error as msg:
            self.report({'ERROR'}, "Failed to connect: is RayPump running?")
            SOCKET.close()
            SOCKET = None
            print(msg)
            return False

        try:
            RAYPUMP_PATH = SOCKET.makefile().readline().rstrip()
            if ('?' in RAYPUMP_PATH):
                raise error('Path has "?"')
        except Exception:
            self.report({'ERROR'}, "Failed to receive RayPump's path from RayPump; please use only ASCII paths")
            SOCKET.close()
            SOCKET = None
            return False

        self.report({'INFO'}, "Connected with RayPump")
        the_version = json.dumps({'VERSION': RAYPUMP_VERSION})
        SOCKET.sendall(bytes(the_version, 'UTF-8'))
        return True

    # section: fixes some missing paths (textures, fonts) that helps to pack the scene for RayPump farm
    def fix(self, context):
        fixApplied = False
        for image in bpy.data.images:
            path = image.filepath
            if path:
                if not os.path.exists(path):
                    print(("Image path: " + image.filepath + " does not exist"))
                    image.filepath = ""
                    fixApplied = True

        for font in bpy.data.fonts:
            path = font.filepath
            if path and path != '<builtin>':
                if not os.path.exists(path):
                    print(("Font path: " + font.filepath + " does not exists"))
                    font.filepath = ""
                    fixApplied = True

        if fixApplied:
            self.report({'INFO'}, 'Invalid entries removed')
            print("Invalid entries removed")

        return fixApplied

    # section: main method called after clicking the button by user
    def execute(self, context):
        global SOCKET, RAYPUMP_PATH
        external_paths = []

        if (self.connect(context) is False):
            print('Aborting due to connecting error')
            return {'CANCELLED'}

        if (bpy.context.scene.render.image_settings.file_format == 'OPEN_EXR_MULTILAYER'):
            print('Multilayer EXR does not support tiles. Turning tiles off')
            self.report({'WARNING'}, 'Turning tiles off for Multilayer EXR')
            bpy.context.scene.ray_pump_use_tiles = False
            
        if (bpy.context.scene.raypump_jobtype == 'FREE') and (bpy.context.scene.ray_pump_use_tiles is True):
            print('Free jobs do not use tiles. Turning tiles off')
            self.report({'WARNING'}, 'Turning tiles off for Free job')
            bpy.context.scene.ray_pump_use_tiles = False

        if (bpy.context.scene.camera.data.type == 'PANO'):
            print('Panoramic camera does not support tiles. Turning tiles off')
            self.report({'WARNING'}, 'Turning tiles off for Panoramic camera')
            bpy.context.scene.ray_pump_use_tiles = False
            
        try:
            bpy.ops.wm.save_mainfile()    # save actual state to main .blend - this is temporary @TODO NOT OPTIMAL
        except RuntimeError as msg:
            print(msg)

        original_fpath = bpy.data.filepath
        simplifiedName = '' + os.path.basename(original_fpath)
        simplifiedName = simplifiedName.replace("_", "-")
        destination_fpath = RAYPUMP_PATH + "/" + simplifiedName.encode('ascii', 'ignore').decode('utf8')

        # section: changes below will be saved to the RayPump's scene copy
        # ---------------------------------------------------------------

        #getting all the linked files
        try:
            bpy.ops.object.make_local(type='ALL')
        except RuntimeError as msg:
            print(msg)


        #required to work with external paths
        bpy.ops.file.make_paths_absolute()

        #getting all the fluid cache paths
        bpy.ops.file.make_paths_absolute()
        for object in bpy.data.objects:
            for modifier in object.modifiers:
                if (modifier.name == "Fluidsim"):
                    if (modifier.settings.type == "DOMAIN"):
                        external_paths.append(os.path.abspath(modifier.settings.filepath))
                        object.modifiers["Fluidsim"].settings.filepath = "//"

        #getting image sequences and movie files for textures (not properly packed with blend file)
        for image in bpy.data.images:
            if (image.source == "SEQUENCE") or (image.source == "MOVIE"):
                nameSplit = os.path.split(os.path.abspath(image.filepath_raw))
                external_paths.append(nameSplit[0])
                image.filepath = "//" + nameSplit[1]
                image.filepath_raw = image.filepath

        ## OTHER EXTERNAL PATHS CAN BE ADDED HERE

        context.scene.update

        try:
            bpy.ops.file.pack_all()
        except RuntimeError:
            try:
                self.fix(None)   # @todo add content variable
                bpy.ops.file.pack_all()
            except RuntimeError as msg:
                self.report({'WARNING'}, "Packing has failed (missing data?)")
                print(msg)

        try:
            bpy.ops.wm.save_as_mainfile(filepath=destination_fpath, copy=True)
        except RuntimeError as msg:
            print(msg)

        # ---------------------------------------------------------------
        # endsection: reopen main blend
        try:
            bpy.ops.wm.open_mainfile(filepath=original_fpath)
        except RuntimeError as msg:
            self.report({'WARNING'}, "Original scene could not be reopened")
            print(msg)

        try:
            the_dump = json.dumps({
                'FRAME_CURRENT': bpy.context.scene.frame_current,
                'FRAME_START': bpy.context.scene.frame_start,
                'FRAME_END': bpy.context.scene.frame_end,
                'JOB_TYPE': bpy.context.scene.raypump_jobtype,
                'EXTERNAL_PATHS': external_paths,
                'VERSION_CYCLE': bpy.app.version_cycle,
                'SCHEDULE': destination_fpath,
                'RESOLUTION': context.scene.render.resolution_x * context.scene.render.resolution_y * (context.scene.render.resolution_percentage / 100),
                'SAMPLE_COUNT': context.scene.cycles.samples,
                'USE_TILES': bpy.context.scene.ray_pump_use_tiles
                })
            SOCKET.sendall(bytes(the_dump, 'UTF-8'))

        except socket.error as msg:
            self.report({'ERROR'}, "Error connecting RayPump client")
            SOCKET = None
            return {'CANCELLED'}

        SynchroSuccessful = SOCKET.makefile().readline().rstrip()
        if (SynchroSuccessful == 'SUCCESS'):
            self.report({'INFO'}, 'Job send')
        else:
            self.report({'ERROR'}, 'Failed to schedule. Check RayPump messages')
        return {'FINISHED'}


def init_properties():
    bpy.types.Scene.raypump_jobtype = EnumProperty(
        items=[('FREE', 'Free', 'Suitable for less demanding jobs (limited daily)'),
                ('STATIC', 'Static', 'Renders current frame using Render Points'),
                ('ANIMATION', 'Animation', 'Renders animation using Render Points')
                ],
        default='FREE',
        name="Job Type")

    # todo: either addon, either blender setting (currently not used, anyway)
    bpy.types.Scene.ray_pump_path = StringProperty(
        name="RayPump (exe)",
        subtype="FILE_PATH",
        description="Path to RayPump executable")

    bpy.types.Scene.ray_pump_use_tiles = BoolProperty(
        name="Use tiles",
        description="Split bigger jobs. Does not support Multilayer-EXR!",
        default=False)


class RayPumpPanel(bpy.types.Panel):
    init_properties()
    """Creates a Panel in the scene context of the properties editor"""
    bl_label = "RayPump II Online Cycles Accelerator"
    bl_idname = "SCENE_PT_layout"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # section: set
        row = layout.row()
        split = row.split(percentage=0.7)
        col = split.column()
        col.prop(scene, "raypump_jobtype", text="Job Type")
        col = split.column()
        col.prop(scene, "ray_pump_use_tiles")

        # section: go
        row = layout.row()
        row.scale_y = 1.6
        row.operator("object.raypump_message_operator", icon='RENDER_STILL')

        row = layout.row()
        split = row.split()
        col = split.column()
        col.operator("object.raypump_view_operator")
        col = split.column()
        col.operator('wm.url_open', text='Help', icon='URL').url = "http://www.raypump.com/help/4-first-time-step-by-step-instruction"


def register():
    bpy.utils.register_class(RayPumpPanel)
    bpy.utils.register_class(MessageRenderOperator)
    bpy.utils.register_class(MessageViewOperator)


def unregister():
    bpy.utils.unregister_class(MessageViewOperator)
    bpy.utils.unregister_class(MessageRenderOperator)
    bpy.utils.unregister_class(RayPumpPanel)

if __name__ == "__main__":
    register()