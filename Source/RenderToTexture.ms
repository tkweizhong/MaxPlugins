macroScript RenderToTexture
category: "Custom Tools"
buttontext: "CustomedRenderToTexture"
(
	dir = getThisScriptFilename()
	dir = (getFilenamePath dir)
	print dir
    python.ExecuteFile (dir + "\..\ModelCombineTexs.py")
)

