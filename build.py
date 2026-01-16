import PyInstaller.__main__
import customtkinter
import os

ctk_path = os.path.dirname(customtkinter.__file__)
sep = ';'

print(f"Building HuaweiFlashTool...")
print(f"CTK Path: {ctk_path}")

PyInstaller.__main__.run([
    'Huawei_FlashTool.py',
    '--name=HuaweiFlashTool',
    '--onefile',
    '--noconsole',
    '--windowed',
    '--icon=app.ico',
    f'--add-data={ctk_path}{sep}customtkinter',
    # f'--add-data=zyxel_logo.png{sep}.', # Убрано
    # f'--add-data=qr_code.png{sep}.',    # Убрано (функционал удален)
    '--clean',
    '--noconfirm',
])