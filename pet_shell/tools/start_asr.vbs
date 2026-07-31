Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\15263\astrbot_plugin_desktop_pet\pet_shell\tools"
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & sh.CurrentDirectory & "\start_asr.ps1""", 0, False
