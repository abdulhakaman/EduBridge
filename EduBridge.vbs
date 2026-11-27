Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("Wscript.Shell")
folder = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
pythonw = folder & "\.venv\Scripts\pythonw.exe"
If Not files.FileExists(pythonw) Then pythonw = "pythonw"
shell.Run """" & pythonw & """ """ & folder & "\desktop_app.py""", 0, False
