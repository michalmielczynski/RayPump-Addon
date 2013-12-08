# This is still in progress!
# The code is working, but needs some cleanup/comments
# The UI freezes while we wait for RayPumpto be ready
bl_info = {
    'name': 'RayPump Online Accelerator',
    'author': 'michal.mielczynski@gmail.com, tiago.shibata@gmail.com',
    'version': '(0, 3, 4)',
    'blender': (2, 6, 0),
    'location': 'Properties > Render > RayPump.com',
    'description': 'Easy to use free online GPU-farm for Cycles',
    'category': 'Render'
    }

import bpy
import socket
import json
import os
import time
import itertools

from bpy.props import *
from subprocess import call

TCP_IP = '127.0.0.1'
TCP_PORT = 5005
SOCKET = None
RAYPUMP_PATH = None
RAYPUMP_VERSION = 0.993 # what version we will connect to?

class MessageRenderOperator(bpy.types.Operator):
    bl_idname = "object.raypump_message_operator"
    bl_label = "Save & Send To RayPump"
    bl_description = "Saves, sends and schedules current scene to the RayPump Accelerator" 
    
    def connect(self):
        global SOCKET, RAYPUMP_PATH, TCP_IP, TCP_PORT
        print("New connection!")
        if SOCKET != None:
            SOCKET.close
        # Create a connection:
        try:
            SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except socket.error as msg:
            self.report({'ERROR'}, "Cannot create a socket")
            SOCKET = None
            return {'CANCELLED'}
        
        try:
            SOCKET.connect((TCP_IP, TCP_PORT))  # connect if RayPump is already open
        except socket.error:
            try:
                call("raypump&", shell=True)    # else start it!
            except Exception as msg:
                print(msg)
                self.report({'ERROR'}, "Can't start RayPump!")
                SOCKET = None
                return {'CANCELLED'}
            for _ in itertools.repeat(None, 20):
                # try to connect, waiting some time for the application to start:
                if (SOCKET.connect_ex((TCP_IP, TCP_PORT)) == 0):
                    break
                time.sleep(.3)
            else:
                self.report({'ERROR'}, "Can't connect to RayPump!")
                SOCKET = None
                return {'CANCELLED'}
        
        RAYPUMP_PATH = SOCKET.makefile().readline().rstrip()
        self.report({'INFO'}, "Connected with RayPump")
        the_version = json.dumps({
            'VERSION':RAYPUMP_VERSION
        })
        SOCKET.sendall(bytes(the_version, 'UTF-8'))
    
    def remove_missing(self):
        if bpy.context.scene.ignore_missing_textures:
            for image in bpy.data.images:
                path = image.filepath
                if path:
                    if not os.path.exists(path):
                        print("Image path: " + image.filepath + " does not exist")
                        image.filepath = ""
            return True
        else:
            self.report({'ERROR'}, "Packing has failed (missing textures?)")
            return False
    
    
    def execute(self, context):
        global SOCKET, RAYPUMP_PATH
        
        try:
            bpy.ops.wm.save_mainfile()        #save actual state to main .blend
        except RuntimeError:
            if (self.remove_missing() == False):
                return {'CANCELLED'}
        
        original_fpath = bpy.data.filepath
        try:
            # These changes will be saved to the RayPump's .blend
            bpy.ops.object.make_local(type='ALL')
            bpy.ops.file.pack_all()
        except RuntimeError as msg:
            if (self.remove_missing() == False):
                bpy.ops.wm.open_mainfile(filepath=original_fpath)        #reopen main blend
                return {'CANCELLED'}
        
        if (SOCKET == None):
            self.connect()
        destination_fpath = RAYPUMP_PATH + "/" + os.path.basename(original_fpath)
        
        for _ in itertools.repeat(None, 2):
            try:
                bpy.ops.wm.save_as_mainfile(filepath=destination_fpath, copy=True)        #save .blend for raypump
                break
            except RuntimeError as msg:    # RayPump was closed and path doesn't exist
                self.connect()
        else:
            self.report({'ERROR'}, "Can't save project for RayPump")
            bpy.ops.wm.open_mainfile(filepath=original_fpath)        #reopen main blend
            return {'CANCELLED'}
        
        bpy.ops.wm.open_mainfile(filepath=original_fpath)        #reopen main blend
        scene = context.scene
        the_dump = json.dumps({
            'SCHEDULE':destination_fpath,
            'FRAME_CURRENT':bpy.context.scene.frame_current,
            'FRAME_START':bpy.context.scene.frame_start,
            'FRAME_END':bpy.context.scene.frame_end,
            'JOB_TYPE':bpy.context.scene.raypump_jobtype
            })
        
        try:
            SOCKET.sendall(bytes(the_dump, 'UTF-8'))
            SynchroSuccessful = SOCKET.makefile().readline().rstrip()
            if (SynchroSuccessful == 'SUCCESS'):
                self.report({'INFO'}, 'Job send')
                return {'FINISHED'}
            if (SynchroSuccessful != 'RETRY'):
                self.report({'ERROR'}, 'Failed to schedule. Check RayPump messages')
                return {'CANCELLED'}
        except socket.error as msg:
            self.connect()  # if socket is closed, reconnect
        
        for _ in itertools.repeat(None, 10):
            try:
                SOCKET.sendall(bytes(the_dump, 'UTF-8'))
                SynchroSuccessful = SOCKET.makefile().readline().rstrip()
                if (SynchroSuccessful == 'SUCCESS'):
                    self.report({'INFO'}, 'Job send')
                    return {'FINISHED'}
                if (SynchroSuccessful == 'RETRY'):
                    time.sleep(.5)
                    continue
                break
            except socket.error as msg:
                self.report({'ERROR'}, 'Connection to RayPump closed')
                return {'CANCELLED'}
        self.report({'ERROR'}, 'Failed to schedule. Check RayPump messages')
        return {'CANCELLED'}


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
    
    bpy.types.Scene.ignore_missing_textures = BoolProperty(
        name="Ignore missing textures",
        description="Send the scene, even if local textures are missing")
    
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
        
        #Section: schedule
        row = layout.row()
        row.scale_y = 2.0
        row.operator("object.raypump_message_operator")
        
        #Section: ignore missing textures
        row = layout.row()
        row.prop(scene, "ignore_missing_textures")
        
        #Section: image format
        row = layout.row()
        row.prop(scene, "raypump_jobtype", text="Job Type")
        
        
def register():
    #init_properties()
    bpy.utils.register_class(RenderPumpPanel)
    bpy.utils.register_class(MessageRenderOperator)

def unregister():
    bpy.utils.unregister_class(RenderPumpPanel)
    bpy.utils.unregister_class(MessageRenderOperator)


if __name__ == "__main__":
    register()
