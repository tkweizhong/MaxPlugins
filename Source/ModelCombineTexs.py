#-*- coding: utf-8 -*-

import asyncio
from PySide2 import QtCore
from PySide2 import QtGui
from PySide2 import QtWidgets
from PySide2.QtWidgets import QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QWidget, QVBoxLayout, QCheckBox, QFileDialog
from PySide2.QtWidgets import QSlider, QLabel, QPushButton, QHeaderView, QAbstractItemView, QMessageBox
import qtmax
from pymxs import runtime as rt
import os
from enum import Enum
from PIL import Image


class RttOperate(Enum):
    COMBINE_MESH = 0,
    COPY_SKIN_DATAS = 1
    UNWRAP_UV = 2
    RENDER_TO_TEXTURE_DIFFUSE = 3
    RENDER_TO_TEXTURE_MASK = 4
    RENDER_TO_TEXTURE_FINIESHED = 5

'''
用于材质最后恢复diffuse,便于对烘焙前后对比;
'''
class MaterailsInfo:
    def __init__(self) -> None:
        self.mats_dic = {}
    def addMeterial(self, object, mat, file_name = None):
        if not mat:
            return
        bitmap = mat.maps[1] #diffuse map
        if not file_name and bitmap:
            file_name = bitmap.filename 
        if file_name:
            self.mats_dic[object] = [mat, file_name]
    def remove(self, obj):
        if obj in self.mats_dic.keys():
            self.mats_dic.pop(obj)
    def reset(self, objs):
        for obj in objs:
            if not obj or ( obj not in  self.mats_dic.keys()):
                continue
            print(f"MaterailsInfo reset {obj}")
            vv = self.mats_dic[obj]
            mat = vv[0]
            if not mat:
                mat = rt.StandardMaterial(isLegacy=True)
                mat.name = obj.name
                obj.material = mat
                mat.mapEnables[1] = True
            bitmap = mat.maps[1] #diffuse map
            if not bitmap:
                bitmap = rt.BitmapTexture()
            mat.maps[1] = bitmap
            bitmap.alphasource = 2 #不导入Alpha,导入Alpha会按照预乘Alpha处理。导致渲染到纹理后不正确。
            bitmap.preMultAlpha  = False
            bitmap.filename = vv[1]
            obj.material = vv[0]
            bitmap.reload()
        for mat in rt.scenematerials:
         	mat.showInViewport = True
        

class RenderTargetTextureInfo:
    def __init__(self):
        self.has_alpha = False
        self.diffuse_path = ""
        self.alpha_mask_path = ""
    def combineDiffuseAndAlphaMask(self):
        if not os.path.exists(self.diffuse_path) or not os.path.exists(self.alpha_mask_path):
            showMessageBox(title="Error", message_type=QMessageBox.Critical, message="Render To Texture错误,未正确渲染输出到目标")
            return
        if not self.has_alpha:
            '''
            使用RTT进行贴图传递,会A通道写坏: uv间隔区域为黑色,其他区域为白色,实际不存在A通道时需要整个都是白色,直接剔除就好;
            '''
            image = Image.open(self.diffuse_path).convert("RGB")
            image.save(self.diffuse_path)
        else:
            diffuse_image = Image.open(self.diffuse_path).convert("RGB")
            r, g, b = diffuse_image.split()
            # print(f"combineDiffuseAndAlphaMask {self.diffuse_path}  {r} {g} {b}")
            alpha_mask_image = Image.open(self.alpha_mask_path).convert("RGB")
            r1, g1, b1 = alpha_mask_image.split()
            # print(f"combineDiffuseAndAlphaMask {self.alpha_mask_path}  {r1}")
            diffuse_image = Image.merge("RGBA",(r, g, b, r1))
            diffuse_image.save(self.diffuse_path)
            

g_rtt_operate_step = RttOperate.COMBINE_MESH
g_widget_width = 500
g_widget_height = 250
g_enable_combine_submesh = True
g_endble_auto_unwrap_uv = False
g_combined_id = 0

def sortCmp(a):
    #print(f"sortCmp ==== {a.name}")
    return a.faces.count
'''
卸载场景物件;
'''
def unloadSceneObjects(objs):
    if not (objs and len(objs) > 0):
        return
    rt.delete(objs)   

'''
加载fbx文件;
'''
async def loadFbxFile(self, file_path):
    setRendererInfo()
    rt.resetMaxFile(rt.name('noPrompt'))
    self.renders = []
    if file_path:
        rt.importFile(file_path, rt.readvalue(rt.StringStream('#noPrompt')))
        all_objects = rt.objects
        self.mats_info = MaterailsInfo() 
        for obj in all_objects:
            if hasModifier(obj, rt.Editable_Poly) or rt.iskindOf(obj, rt.Editable_mesh) or hasModifier(obj, rt.Skin):
                self.renders.append(obj)
                print(f"loadFbxFile===== : {obj.name}")
    await asyncio.sleep(1)
    has_error = False
    texture_folder = os.path.dirname(self.save_texture_path) 
    for obj in self.renders:
        current_mat = obj.material
        material = rt.StandardMaterial(isLegacy=True)
        if current_mat is not None:
            material.name = current_mat.name
        else:
            material.name = obj.name
        file_name = ""
        bitmap_texture = rt.BitmapTexture()
        for p in {'tga', 'png', 'jpg'}:
            file_path = f"{texture_folder}/{obj.name}_d.{p}"
            if os.path.exists(file_path):
                file_name = file_path
                # print(file_path)
                break
        if file_name == "" or file_name == " ":
            has_error = True
        bitmap_texture.filename = file_name
        bitmap_texture.alphasource = 2 #不导入Alpha,导入Alpha会按照预乘Alpha处理。导致渲染到纹理后不正确。
        bitmap_texture.preMultAlpha  = False
        # material.Ambient = bitmap_texture
        material.mapEnables[1] = True
        material.maps[1] = bitmap_texture
        obj.material = material
    if has_error:
        def ok_callback():
            rt.execute("macros.run \"Medit Tools\" \"advanced_material_editor\"")
            setRendererInfo()
        showMessageBox(title="Error", message_type=QMessageBox.Critical, message="命名不规范,材质diffuse纹理链接不正确,请手动链接", button_operate=QMessageBox.Ok, ok_callback=ok_callback)

    rt.redrawViews()


def hasAlphaChannel(image):
    if not image:
        return False
    bands = image.getbands()
    return ('A' in bands) or ('a' in bands)

'''
处理diffuse alpha mask:
当前局内shader使用alha作为自发光mask;使用贴图传递功能需要额外处理alpha mask;
'''
def processAlphaMask(self):
    self.need_delete_file_path = []
    for obj in self.select_objs:
        material = obj.material
        if not material or not material.maps[1]:
            continue
        bitmap = material.maps[1]
        texture_path = bitmap.filename
        if not os.path.exists(texture_path):
            continue
        # print(texture_path)
        image = Image.open(texture_path)
        if hasAlphaChannel(image):
            alpha = image.getchannel('A')
            image = Image.merge('RGB', (alpha, alpha, alpha))
            self.render_target_texture_info.has_alpha = True
        else:
            def blackColor(x):
                return 0
            image = Image.eval(image, blackColor)
        dir_name, full_file_name = os.path.split(texture_path)
        file_name, file_ext = os.path.splitext(full_file_name)
        new_file_name = f"{file_name}_aaa"
        new_texture_path = texture_path.replace(file_name, new_file_name)
        image.save(new_texture_path)
        bitmap.filename = new_texture_path
        material.maps[1].reload()    
        self.need_delete_file_path.append(new_texture_path)

'''
是否包含某个修改器;
'''
def hasModifier(node, modifier_type):
    if rt.iskindOf(node, modifier_type):
        return True
    for mod in node.modifiers:
        is_the_modifier = rt.iskindOf(mod, modifier_type)
        if is_the_modifier:
            return True
    return False

'''
信息提示框;
'''
def showMessageBox(title, message_type,  message, button_operate = QMessageBox.Ok, ok_callback = None, cancel_callback = None):
    msg_box = QMessageBox()
    msg_box.setIcon(message_type)
    msg_box.setText(message)
    msg_box.setWindowTitle(title)
    if button_operate & QMessageBox.Cancel:
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(cancel_callback)
        msg_box.addButton(cancel_button, QMessageBox.RejectRole)
    ok_button =  QPushButton("OK")
    ok_button.clicked.connect(ok_callback)
    msg_box.addButton(ok_button, QMessageBox.AcceptRole)
    msg_box.exec_()

def getCombineName(file_path):
    if not file_path:
        return
    global g_combined_id
    file_name =  os.path.basename(file_path).split('/')[-1]
    file_name = file_name.split('.')[0]
    if g_combined_id == 0:  
        file_name = file_name.replace('_L', '_Combined_L')
    else:
        file_name = file_name.replace('_L', f'_Combined_{g_combined_id}_L')
    return file_name

'''
设置渲染器
'''
def setRendererInfo():
    #use scanline renderer
    rt.renderers.current = rt.Default_Scanline_Renderer()
    scanlineRender = rt.renderers.current # rt.scanlineRender
    scanlineRender.antiAliasing = True
    scanlineRender.filterMaps = True
    scanlineRender.antiAliasFilter = rt.Catmull_Rom()
    scanlineRender.enablePixelSampler  = True
    scanlineRender.globalSamplerAdaptive = True
    scanlineRender.globalSamplerClassByName = "Hammersley"
    scanlineRender.globalSamplerEnabled = True
    # scanlineRender.globalSamplerAdaptiveThresh = 1.0
    scanlineRender.globalSamplerQuality = 1
    scanlineRender.imageMotionBlur = False #关闭运动模糊;


'''
默认使用unfold自动展uv;
也可以调用用旧版本的LSCMSolve();
'''
def unwrapUV(self, target_obj):
    global g_rtt_operate_step
    if not g_endble_auto_unwrap_uv:
        #go to the next state if you have manual unfold uv
        def call_back():
            global g_rtt_operate_step
            g_rtt_operate_step = RttOperate.COPY_SKIN_DATAS
            self.apply_btn.setText("Copy Skin Datas")
            self.apply()
        showMessageBox("Information", QMessageBox.Warning, "已经手动展好UV了么", button_operate=QMessageBox.Ok | QMessageBox.Cancel, ok_callback=call_back)
        return
    unwrap_uv_modifier = None
    for modifier in target_obj.modifiers:
        if rt.iskindOf(modifier, rt.Unwrap_UVW):
            rt.delete(modifier)
    if unwrap_uv_modifier is None:
        unwrap_uv_modifier = rt.Unwrap_UVW()
        rt.addModifier(target_obj, unwrap_uv_modifier)
    unwrap_uv_modifier.Unfold3DSolve()
    #to next state
    g_rtt_operate_step = RttOperate.COPY_SKIN_DATAS
    self.apply_btn.setText("Copy Skin Datas")
    self.apply()

'''
渲染到纹理
'''
def renderToTexture(self, target_object, is_emission_mask = False):
    if not target_object:
        showMessageBox(title="Target object is None", message_type=QMessageBox.Critical, message="Target object is None")
        return
    
    setRendererInfo()
    rt.select(target_object)

    target_object.iNodeBakeProperties.removeAllBakeElements()

    #3ds Max2023,多次操作，可能会让链接的bitmap丢失，但纹理路径还在，reload解决.
    for obj in self.renders:
        obj.material.maps[1].reload()

    diffuse = rt.diffuseMap()
    diffuse.outputSzX = diffuse.outputSzY = 1024
    texture_file_path = self.save_texture_path
    if not (g_combined_id == 0):
         texture_file_path = texture_file_path.replace('_Combined_L', f'_Combined_{g_combined_id}_L')
    self.render_target_texture_info.diffuse_path = texture_file_path
    diffuse.filterOn = True
    diffuse.shadowsOn = False
    diffuse.lightingOn = False
    diffuse.targetMapSlotName = ""
    diffuse.elementName = "DiffuseMap"
    diffuse.filenameUnique = True
    if is_emission_mask:
        _, full_file_name = os.path.split(texture_file_path)
        file_name, _ = os.path.splitext(full_file_name)
        new_file_name = f"{file_name}_aaa"
        texture_file_path = texture_file_path.replace(file_name, new_file_name)
        try:
            self.need_delete_file_path.append(texture_file_path)
            self.render_target_texture_info.alpha_mask_path = texture_file_path
        except ValueError as e:
            self.need_delete_file_path = []
            self.need_delete_file_path.append(texture_file_path)
    diffuse.fileType = (texture_file_path) #rt.getFilenameType(texture_file_path) 
    diffuse.filename = diffuse.fileName = texture_file_path #rt.filenameFromPath(texture_file_path) 
    
    if os.path.exists(texture_file_path):
        # print(f"renderToTexture remove {texture_file_path}")
        os.remove(texture_file_path)
    # if not is_emission_mask:
    #     self.mats_info.addMeterial(target_object, target_object.material, texture_file_path)
    diffuse.enabled = True
    # print(diffuse.fileType)
    # print(f"fileType {diffuse.fileType} rt.getFilenameType : {rt.getFilenameType(texture_file_path)}\n")
    # print(f"fileName {diffuse.fileName} \n")
    target_object.iNodeBakeProperties.addBakeElement(diffuse)
    target_object.iNodeBakeProperties.bakeEnabled = True
    target_object.iNodeBakeProperties.flags = 1
    target_object.iNodeBakeProperties.bakeChannel = 1 #channel to bake
    target_object.iNodeBakeProperties.nDilations = 1 #expand the texturea bit

    target_object.iNodeBakeProjProperties.enabled = True
    target_object.iNodeBakeProjProperties.subObjBakeChannel = 1
    target_object.iNodeBakeProjProperties.hitResolveMode  = "closest"
    target_object.iNodeBakeProjProperties.useCage = False
    target_object.iNodeBakeProjProperties.rayOffset = 400

    projection_modifier = None
    for modifier in target_object.modifiers:
        if rt.iskindOf(modifier, rt.Projection):
            projection_modifier = modifier
            break
    if projection_modifier is None:
        projection_modifier = rt.Projection()
        rt.addModifier(target_object, projection_modifier)
    projection_modifier.deleteAll()
    #不用框架优化，开启后在Max2023 RTT后结果不正确.不知道是什么问题;
    # projection_modifier.resetCage()
    # projection_modifier.pushCage(10) 
    projection_modifier.displayCage = True

    for obj in self.select_objs:
        projection_modifier.addObjectNode(obj)
    projection_modifier.mapChannel = 1
    
    #open RTT dialog
    rt.execute("macros.run \"Render\" \"BakeDialog\"")
    rt.execute("SetDialogPos gTextureBakeDialog [500,200]")
    #presss bake button
    rt.execute("gTextureBakeDialog.bRender.pressed()")
    #clsoe RTT dialog
    rt.execute("destroydialog gTextureBakeDialog")
    
    file_path =  texture_file_path
    if is_emission_mask:
        file_path = file_path.replace("_aaa", "")
    
    bitmap_texture = rt.BitmapTexture()
    bitmap_texture.filename = file_path
    #给烘焙后的模型赋一个材质,不使用复合材质;
    material = rt.StandardMaterial(isLegacy=True)
    material.name = target_object.name
    target_object.material = material
    material.mapEnables[1] = True
    material.maps[1] = bitmap_texture
    bitmap_texture.reload()
    
    global g_rtt_operate_step
    if g_rtt_operate_step == RttOperate.RENDER_TO_TEXTURE_DIFFUSE:
        g_rtt_operate_step = RttOperate.RENDER_TO_TEXTURE_MASK
        self.apply_btn.setText("Render To Mask Texture")
        self.apply()
    elif g_rtt_operate_step == RttOperate.RENDER_TO_TEXTURE_MASK:
        g_rtt_operate_step = RttOperate.RENDER_TO_TEXTURE_FINIESHED
        self.apply_btn.setText("Render To Texture Finished")
        self.apply()
    rt.redrawViews()

'''
拷贝蒙皮信息;使用skin_wrap处理方式;
'''
def copySkinDatas(self, target_obj):
    for modifier in target_obj.modifiers:
        if rt.iskindOf(modifier, rt.Skin_Wrap):
            rt.delete(modifier)
    
    skin_wrap_modifier = rt.Skin_Wrap()
    skin_wrap_modifier.engine = 1 #顶点变形;
    skin_wrap_modifier.falloff = 0.001
    skin_wrap_modifier.distance = 0.001
    skin_wrap_modifier.faceLimit = 3
    skin_wrap_modifier.threshold = 0.01
    skin_wrap_modifier.weightAllVerts = True

    for i in  range(0, len(self.select_objs)):
        skin_wrap_modifier.meshList[i] = self.select_objs[i]
    rt.addModifier(target_obj, skin_wrap_modifier)
    skin_wrap_modifier.meshDeformOps.convertToSkin(True)
    rt.redrawViews()
'''
合并submesh;
'''
def combineSelectedMesh(self):
    objs_len = len(self.select_objs)
    if objs_len > 1:
        combined_name = self.fbx_file_path_text.text()
        combined_name = getCombineName(combined_name)
        merged_mesh = rt.copy(self.select_objs[0])
        merged_mesh = rt.convertToMesh(merged_mesh)
        merged_mesh.name = combined_name
        # self.save_texture_path = os.path.join(self.save_texture_path, f"{combined_name}_d.tga")
        for obj in self.select_objs[1:]:
            copied_mesh = rt.copy(obj)
            rt.attach(merged_mesh, rt.convertToMesh(copied_mesh))
        
        material = rt.StandardMaterial(isLegacy=True)
        material.name = merged_mesh.name
        merged_mesh.material = material
        rt.select(merged_mesh)
        return merged_mesh
    else:
        showMessageBox(title="Error", message_type=QMessageBox.Critical, message="请至少选择两个要合并的网格")

'''
删除修改器
need_collapse: 是否需要塌陷，只塌陷当前修改器。不对栈中其他修改器生效;
这里实现只能塌陷在栈顶的修改器,非栈顶的修改器会出错;
尝试使用如下方式处理: https://deniskorkh.wordpress.com/2012/01/13/5/
这里实现思路是重新复制了一个Object,modifier list重排后,再逐个添加到新Object上;
在Max2023中,实际运行看起来没啥问题,但会导致部分蒙皮数据出问题,具体细节没深究,我在修改Object过程中会确保需要塌陷的Modifier在栈顶;
'''
def deleteModifier(obj, modifier_type, need_collapse = False):
    if not obj:
        return
    index = -1
    modfiler_len = obj.modifiers.count
    for i in range(0, modfiler_len) :
        m = obj.modifiers[i]
        if rt.iskindOf(m, modifier_type):
            index = i
            break
    if index < 0:
        return
    if need_collapse:
        rt.execute(f"maxOps.CollapseNodeTo $ {modfiler_len} off")
    else:
        rt.deleteModifier(obj, index+1)

class PyMaxDockWidget(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super(PyMaxDockWidget, self).__init__(parent)
        self.open_file_btn = None
        self.fbx_file_path_text = None
        self.target_combined_obj = None
        self.select_objs = []
        self.renders = []
        self.render_target_texture_info = RenderTargetTextureInfo()
        self.setWindowFlags(QtCore.Qt.Tool)
        self.setWindowTitle('ModelCombine Tex Window')
        self.initUI()
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        qtmax.DisableMaxAcceleratorsOnFocus(self, True)

    def initUI(self):
        setRendererInfo()
        main_layout = QtWidgets.QVBoxLayout()

        file_input_layout = QtWidgets.QHBoxLayout()
        self.fbx_file_path_text = QtWidgets.QLineEdit()
        self.fbx_file_path_text.setMinimumHeight(30)
        self.fbx_file_path_text.setMaximumHeight(40)
        file_input_layout.addWidget(self.fbx_file_path_text)

        self.open_file_btn = QtWidgets.QPushButton("Open File")
        self.open_file_btn.setMinimumHeight(30)
        self.open_file_btn.setMaximumHeight(40)
        self.open_file_btn.clicked.connect(self.openFolder)
        file_input_layout.addWidget(self.open_file_btn)
        main_layout.addLayout(file_input_layout)

        file_input_layout = QtWidgets.QHBoxLayout()
        self.save_texture_path_text = QtWidgets.QLineEdit()
        self.save_texture_path_text.setMinimumHeight(30)
        self.save_texture_path_text.setMaximumHeight(40)
        file_input_layout.addWidget(self.save_texture_path_text)

        self.save_texture_btn = QtWidgets.QPushButton("Save Texture File")
        self.save_texture_btn.setMinimumHeight(30)
        self.save_texture_btn.setMaximumHeight(40)
        self.save_texture_btn.clicked.connect(self.saveTexApply)
        file_input_layout.addWidget(self.save_texture_btn)
        main_layout.addLayout(file_input_layout)

        layout = QVBoxLayout()
        
        self.table_widget = QTableWidget(self)
        self.table_widget.setColumnCount(2)
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.table_widget.setHorizontalHeaderLabels(["Object Name", "Select"])
        layout.addWidget(self.table_widget)
        main_layout.addLayout(layout)

        hbox = QtWidgets.QHBoxLayout()
        self.auto_unwrapUV_checkbox = QCheckBox("Auto Unwrap UV")
        self.auto_unwrapUV_checkbox.setChecked(g_endble_auto_unwrap_uv)
        self.auto_unwrapUV_checkbox.clicked.connect(  self.onClickAutoUnwrapUVCheckBox )
        hbox.addWidget(self.auto_unwrapUV_checkbox)
        delete_button = QtWidgets.QPushButton("Clear Scenes")
        delete_button.clicked.connect(self.clearSourceObjet)
        hbox.addWidget(delete_button)
        main_layout.addLayout(hbox)

        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.clicked.connect(self.apply)
        main_layout.addWidget(self.apply_btn)
        widget = QtWidgets.QWidget()
        widget.setLayout(main_layout)
        self.setWidget(widget)
        self.resize(g_widget_width, g_widget_height)

    def clearSourceObjet(self):
        legal = self.checkCombinedTargetLegal()
        print(f"clearSourceObjet {legal}")
        if not legal:
            return
        global g_rtt_operate_step
        if g_rtt_operate_step != RttOperate.RENDER_TO_TEXTURE_FINIESHED:
            showMessageBox(title="Error", message_type=QMessageBox.Critical, message="先合并完,再清理原始Object")
            return

        for obj in self.select_objs:
            if obj in self.renders:
                self.renders.remove(obj)
            self.mats_info.remove(obj)
        
        unloadSceneObjects(self.select_objs)
        self.select_objs = []
        self.updateObjsShowListUI()
        self.resize(g_widget_width, g_widget_height)
        
        target_obj = rt.getCurrentSelection()[0]
        deleteModifier(target_obj, rt.Projection)
        deleteModifier(target_obj, rt.Skin_Wrap)
        rt.redrawViews()

        try:
            for path in self.need_delete_file_path:
                if os.path.exists(path):
                    os.remove(path)
        except Exception as e:
            print(f"remove file failed {e}")
        finally:
            g_rtt_operate_step = RttOperate.COMBINE_MESH
            self.apply_btn.setText("Apply")
    
    def onClickAutoUnwrapUVCheckBox(self, state):
        global g_endble_auto_unwrap_uv
        # print(f"============================= {state}")
        g_endble_auto_unwrap_uv = state

    '''
    手动展uv时,会存在从外部导入展好uv的模型,这个模型需要重新手动选中。且是第一个被选中的。
    '''
    def checkCombinedTargetLegal(self):
        selected_objs = rt.getCurrentSelection()
        selected_obj = None
        if selected_objs and len(selected_objs) > 0:
            selected_obj = selected_objs[0]
        is_legal = True
        if not selected_obj:
            is_legal = False
        if is_legal:
            for obj in self.renders:
                if obj == selected_obj:
                    is_legal =  False
                    break
        if not is_legal:
            showMessageBox(title="Error", message_type=QMessageBox.Critical, message="先手动选中合并后的模型,再执行下一步操作")
        return is_legal
    
    def combineMeshAndSkindatas(self):
        global g_rtt_operate_step
        target_obj = combineSelectedMesh(self)
        if not target_obj:
            return 
        self.target_combined_obj = target_obj
        copySkinDatas(self, target_obj)
        g_rtt_operate_step = RttOperate.UNWRAP_UV
        self.apply_btn.setText("UnWrap UV")
        self.apply()
        return target_obj
    
    def combineMesh(self):
        global g_rtt_operate_step
        target_obj = combineSelectedMesh(self)
        if not target_obj:
            return 
        self.target_combined_obj = target_obj
        g_rtt_operate_step = RttOperate.UNWRAP_UV
        self.apply_btn.setText("UnWrap UV")
        self.apply()
        
    def copySkindatas(self, target_obj):
        global g_rtt_operate_step
        copySkinDatas(self, target_obj)
        g_rtt_operate_step = RttOperate.RENDER_TO_TEXTURE_DIFFUSE
        self.apply_btn.setText("Render To Diffuse Texture")
        self.apply()

    '''
    记录原始材质，烘焙完后还原回来;
    '''
    def recordOriginalMats(self):
        success = True
        for obj in self.select_objs:
            if not (obj and obj.material):
                continue
            material = obj.material
            bitmap = material.maps[1] 
            if not bitmap:
                success = False
                break
            file_name = bitmap.filename
            self.mats_info.addMeterial(obj, material, file_name)
        if not success:
            showMessageBox(title="Error", message_type=QMessageBox.Critical, message="存在未正确链接diffuse纹理的材质,请检查")
        return success
    
    def apply(self):
        # if g_rtt_operate_step == RttOperate.COMBINE_MESH:        
        #     self.combineMeshAndSkindatas()
        if g_rtt_operate_step == RttOperate.COMBINE_MESH:
            self.combineMesh()
        elif g_rtt_operate_step == RttOperate.UNWRAP_UV:
            legal = self.checkCombinedTargetLegal()
            if not legal:
                return
            target_obj = rt.getCurrentSelection()[0] #self.target_combined_obj 
            unwrapUV(self, target_obj)
        elif g_rtt_operate_step == RttOperate.COPY_SKIN_DATAS:
            legal = self.checkCombinedTargetLegal()
            if not legal:
                return
            target_obj = rt.getCurrentSelection()[0] #self.target_combined_obj
            #会塌陷栈中所有修改器;这里主要是为了塌陷Unwrap_UVW
            rt.convertTo(target_obj, rt.Editable_Poly)
            self.copySkindatas(target_obj)
        elif g_rtt_operate_step == RttOperate.RENDER_TO_TEXTURE_DIFFUSE:
            legal = self.checkCombinedTargetLegal()
            if not legal:
                return 
            target_obj = rt.getCurrentSelection()[0]
            success = self.recordOriginalMats()
            if not success:
                return
            renderToTexture(self, target_obj)
        elif g_rtt_operate_step == RttOperate.RENDER_TO_TEXTURE_MASK:
            legal = self.checkCombinedTargetLegal()
            if not legal:
                return 
            target_obj = rt.getCurrentSelection()[0]
            processAlphaMask(self)
            renderToTexture(self, target_obj, True)
        elif g_rtt_operate_step == RttOperate.RENDER_TO_TEXTURE_FINIESHED:
            self.render_target_texture_info.combineDiffuseAndAlphaMask()
            self.mats_info.reset(self.select_objs)
            global g_combined_id
            g_combined_id = g_combined_id + 1
        rt.redrawViews()
    
    def updateObjsShowListUI(self):
        print("updateObjsShowListUI self.renders :", self.renders)
        self.table_widget.setRowCount(len(self.renders))
        for row, obj in enumerate(self.renders):
            print(f"updateObjsShowListUI element {obj.name}")
            cell_info = {}
            cell_info['obj'] = obj
            item_name = QTableWidgetItem(obj.name)
            self.table_widget.setItem(row, 0, item_name)
            
            checkbox = QCheckBox()
            cell_info['checkbox'] = checkbox
            checkbox.clicked.connect(lambda cell_info=cell_info, state=None:self.checkboxClicked(cell_info))
            self.table_widget.setCellWidget(row, 1, checkbox)

        self.resize(g_widget_width+1, g_widget_height)
        self.table_widget.resizeColumnsToContents()
        self.table_widget.resizeRowsToContents()
        rt.redrawViews()

    def checkboxClicked(self, cell_info):
        if not self.select_objs:
            self.select_objs = []
        obj = cell_info['obj']
        checkbox = cell_info['checkbox']
        if checkbox.isChecked() and (obj not in self.select_objs):
            self.select_objs.append(obj)
        elif obj in self.select_objs:
            self.select_objs.remove(obj)
        #排序，按照面数从大到小，最大可能保证蒙皮数据准确性;
        self.select_objs.sort(key=sortCmp, reverse=True)
        # for a in self.select_objs:
        #     print(f"after changed: {a}")
        # print("===============")
        rt.select(self.select_objs)
        if len(self.select_objs) < 1:
            rt.clearSelection()

    def saveTexApply(self):
        global g_rtt_operate_step
        options = QtWidgets.QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(self, "Open File", "", "Text Files (*.tga);", options=options)
        if file_path:
            self.save_texture_path_text.setText(file_path)
            self.save_texture_path = file_path

    def openFolder(self):
        global g_rtt_operate_step
        options = QtWidgets.QFileDialog.Options()
        fbx_file_path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "Text Files (*.fbx);", options=options)
        if fbx_file_path:
            self.fbx_file_path_text.setText(fbx_file_path)
            global g_combined_id
            g_combined_id = 0
            #修正save_texture_path_text纹理保存路径;
            fbx_folder = os.path.dirname(fbx_file_path)
            texture_folder = fbx_folder
            texture_folder = texture_folder.replace("Model", 'Texture')
            combined_name = getCombineName(fbx_file_path)
            self.save_texture_path = os.path.join(texture_folder, f"{combined_name}_d.tga")    
            self.save_texture_path_text.setText(self.save_texture_path )

            self.renders = []
            self.select_objs = []
            rt.clearSelection()
            unloadSceneObjects(rt.objects)
            
            g_rtt_operate_step = RttOperate.COMBINE_MESH
            self.apply_btn.setText("Apply")
            asyncio.run(loadFbxFile(self, fbx_file_path))
            self.updateObjsShowListUI()
            self.resize(g_widget_width, g_widget_height)

def main():
    rt.resetMaxFile(rt.name('noPrompt'))
    main_window = qtmax.GetQMaxMainWindow()
    w = PyMaxDockWidget(parent=main_window)
    w.setFloating(True)
    w.show()

if __name__ == '__main__':
    main()
