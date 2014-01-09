bl_info = {
    'name': 'RayPump Online Accelerator',
    'author': 'michal.mielczynski@gmail.com, tiago.shibata@gmail.com',
    'version': '(0, 9, 9, 5)',
    'blender': (2, 6, 6),
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
from subprocess import call

TCP_IP = '127.0.0.1'
TCP_PORT = 5005
SOCKET = None 
RAYPUMP_PATH = None
RAYPUMP_VERSION = 0.995 # what version we will connect to?
        
class ConnectClientOperator(bpy.types.Operator):
    bl_idname = "object.raypump_connect_operator"
    bl_label = "Connect/Show local RayPump"
    bl_description = "(re)Initializes connection with the local RayPump client"

    def execute(self, context):
        global SOCKET, TCP_IP, TCP_PORT, RAYPUMP_PATH
        scene = context.scene       
            
        if (SOCKET != None):
            SOCKET.close()
        try:
            SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except socket.error as msg:
            self.report({'ERROR'}, "Cannot create a socket")
            SOCKET = None
            return {'CANCELLED'}

        try:
            SOCKET.connect((TCP_IP, TCP_PORT))
        except socket.error as msg:
            self.report({'ERROR'}, "Failed to connect: is RayPump running?")
            SOCKET.close()
            SOCKET = None
            return {'CANCELLED'}
        
        try:
            RAYPUMP_PATH = SOCKET.makefile().readline().rstrip()
            if ('?' in RAYPUMP_PATH):
                raise error('Path has "?"')
        except Exception:
            self.report({'ERROR'}, "Failed to receive RayPump's path from RayPump; please use only ASCII paths")
            SOCKET.close()
            SOCKET = None
            return {'CANCELLED'}
        
        self.report({'INFO'}, "Connected with RayPump")
        the_version = json.dumps({
            'VERSION':RAYPUMP_VERSION
        })
        SOCKET.sendall(bytes(the_version, 'UTF-8'))
        return {'FINISHED'}


class MessageRenderOperator(bpy.types.Operator):
    bl_idname = "object.raypump_message_operator"
    bl_label = "Save & Send To RayPump"
    bl_description = "Saves, sends and schedules current scene to the RayPump Accelerator" 

    def execute(self, context):
        global SOCKET, RAYPUMP_PATH
        external_paths = []
        
        if (SOCKET == None):
            self.report({'ERROR'}, "Blender is note connected with RayPump - click Connect/Show button")
            return {'CANCELLED'}
        else:
            bpy.ops.wm.save_mainfile()	#save actual state to main .blend
            
            original_fpath = bpy.data.filepath
            destination_fpath = RAYPUMP_PATH + "/" + os.path.basename(original_fpath)
            
            ## @section: changes below will be saved to the RayPump's scene copy 
            
            #getting all the linked files
            bpy.ops.object.make_local(type='ALL')
            
            #getting all the fluid cache paths
            bpy.ops.file.make_paths_absolute()
            for object in bpy.data.objects:
                for modifier in object.modifiers:
                    if (modifier.name == "Fluidsim"):
                        if (modifier.settings.type == "DOMAIN"):
                            external_paths.append(os.path.abspath(modifier.settings.filepath))
                            object.modifiers["Fluidsim"].settings.filepath = "//"
            
            ## OTHER EXTERNAL PATHS CAN BE ADDED HERE 
            
            context.scene.update
                            
            try:
                bpy.ops.file.pack_all()
                bpy.ops.wm.save_as_mainfile(filepath=destination_fpath, copy=True)	#save .blend for raypump
            except RuntimeError as msg:
                self.report({'ERROR'}, "Packing has failed (missing textures?)")
                print(msg)
                return {'CANCELLED'}
            finally:
                ## @endsection: reopen main blend
                bpy.ops.wm.open_mainfile(filepath=original_fpath)
            
            try:    
                the_dump = json.dumps({
                    'SCHEDULE':destination_fpath,
                    'FRAME_CURRENT':bpy.context.scene.frame_current,
                    'FRAME_START':bpy.context.scene.frame_start,
                    'FRAME_END':bpy.context.scene.frame_end,
                    'JOB_TYPE':bpy.context.scene.raypump_jobtype,
                    'EXTERNAL_PATHS' :external_paths
                    })
                SOCKET.sendall(bytes(the_dump, 'UTF-8'))
                SYNCHRONIZING = True
                
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
	
		
class RemoveMissingPathOperator(bpy.types.Operator):
    bl_idname = "object.raypump_remove_missing_paths_operator"
    bl_label = "Fix Missing Paths"
    bl_description = "Removes invalid file names from the scene"
    
    def execute(self, context):
        fixApplied = False
        for image in bpy.data.images:
            path = image.filepath
            if path:
                if not os.path.exists(path):
                    print("Image path: " + image.filepath + " does not exist")
                    image.filepath = ""
                    fixApplied = True
        
        for font in bpy.data.fonts:
            path = font.filepath
            if path and path!='<builtin>':
                if not os.path.exists(path):
                    print("Font path: " + font.filepath + " does not exists")
                    font.filepath = ""
                    fixApplied = True
                        
        if fixApplied:
            self.report({'INFO'}, 'Invalid entries removed')
        else:
            self.report({'INFO'}, 'No invalid entries found')   
        return {'FINISHED'}

def init_properties():
    bpy.types.Scene.raypump_jobtype = EnumProperty(
        items = [('FREE', 'Free', 'Suitable for less demanding jobs (limited daily)'), 
                ('STATIC', 'Static', 'Renders current frame using Render Points'),
                ('ANIMATION', 'Animation', 'Renders animation using Render Points'), 
                ('STRESS-TEST', 'Stress-Test', 'Estimates cost and test GPU compatibility')
                ],
        default = 'FREE',
        #description = 'Set the way RayPump will treat scheduled job',
        name = "Job Type")
        
    # @todo either addon, either blender setting (currently not used, anyway)
    bpy.types.Scene.ray_pump_path = StringProperty(
        name="RayPump (exe)",
        subtype="FILE_PATH",
        description="Path to RayPump executable")

    

class RenderPumpPanel(bpy.types.Panel):
    init_properties()
    """Creates a Panel in the scene context of the properties editor"""
    bl_label = "RayPump.com"
    bl_idname = "SCENE_PT_layout"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        #Section: connection and textures fix
        row = layout.row(align=True)
        split = row.split(percentage=0.66)
        col = split.column()
        col.operator("object.raypump_connect_operator")
        col = split.column()
        col.operator("object.raypump_remove_missing_paths_operator")
        
        #Section: schedule       
        row = layout.row()
        row.scale_y = 2.0
        row.operator("object.raypump_message_operator")
        
        #Section: image format
        row = layout.row()
        row.prop(scene, "raypump_jobtype", text="Job Type")
        
def register():
    #init_properties()
    bpy.utils.register_class(RenderPumpPanel)
    bpy.utils.register_class(MessageRenderOperator)
    bpy.utils.register_class(ConnectClientOperator)
    bpy.utils.register_class(RemoveMissingPathOperator)

def unregister():
    bpy.utils.unregister_class(RenderPumpPanel)
    bpy.utils.unregister_class(MessageRenderOperator)
    bpy.utils.unregister_class(ConnectClientOperator)
    bpy.utils.unregister_class(RemoveMissingPathOperator)


if __name__ == "__main__":
    register()
