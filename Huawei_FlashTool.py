import os
import sys
import time
import paramiko
from paramiko.ssh_exception import AuthenticationException, SSHException
from scp import SCPClient
from threading import Thread
import customtkinter as ctk
from tkinter import simpledialog, messagebox, filedialog
import tkinter as tk
from PIL import Image
import webbrowser

# Основные настройки устройства
DEVICE_NAME = "Huawei MA5671A/G-010S-P"
BACKUP_DIR = 'backup'
LOGO_FILENAME = 'zyxel_logo.png'

# Тайминги и лимиты
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY = 60
TOTAL_STEPS = 10 

# Реквизиты Zyxel (после прошивки)
NEW_ROUTER_IP = '10.10.1.1'
NEW_ROUTER_USER = 'admin'
NEW_ROUTER_PASS = 'admin'

# Реквизиты Huawei (сток)
HUAWEI_IP = '192.168.1.10'
HUAWEI_USER = 'root'
HUAWEI_PASS = 'root'

# Команды переключения режимов
HUAWEI_SWITCH_CMDS = [
    '/usr/sbin/fw_setenv bootcmd run boot_image1',
    '/usr/sbin/fw_setenv committed_image 1',
    '/sbin/reboot'
]
ZYXEL_SWITCH_CMDS = [
    'twmanu', 'twmanu', 'linuxshell',
    '/usr/sbin/fw_setenv bootcmd run boot_image0',
    '/usr/sbin/fw_setenv committed_image 0',
    'exit', 'system', 'reboot'
]

SWITCH_COMMAND_TIMEOUT = 3 
CRITICAL_COMMAND_TIMEOUT = 5

# Скрипты подготовки разделов и прошивки
CM1_CONTENT = (
    "#!/bin/ash\n"
    "/usr/sbin/fw_setenv asc0 0\n"
    "/usr/sbin/fw_setenv addmtdparts1 \"setenv mtdparts mtdparts=sflash:256k(uboot)ro,512k(uboot_env),7424k(image0),384k(Boot)ro,64k(Env),3648k(ImageA),3648k(ImageB),384k(Config),64k(SECTION_EGIS),2368k@13568k(rootfs) ACTIVE=B softMode=NORMAL\"\n"
    "/usr/sbin/fw_setenv image1_version 3.2.16_160606\n"
    "/usr/sbin/fw_setenv kernel1_offs 0xC00200\n"
    "/usr/sbin/fw_setenv image1_addr 0xB0C00200\n"
)

CM2_CONTENT = (
    "#!/bin/ash\n"
    "/sbin/mtd -e image1 write /tmp/firmware.bin image1\n"
    "/usr/sbin/fw_setenv flash_flash \"setenv flash_flash run select_image boot_image && saveenv && run boot_image1\"\n"
    "/usr/sbin/fw_setenv bootcmd run boot_image1\n"
    "/usr/sbin/fw_setenv image1_is_valid 1\n"
    "/usr/sbin/fw_setenv committed_image 1\n"
    "/sbin/reboot\n"
)

def get_resource_path(filename):
    """ Получение пути к ресурсам для PyInstaller """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, filename)

class App(ctk.CTk):
    FONT_FAMILY = "Segoe UI"

    def __init__(self):
        super().__init__()
        self.title(f"Huawei FlashTool - {DEVICE_NAME}")
        self.geometry("1000x750")
        ctk.set_appearance_mode("Dark") 
        ctk.set_default_color_theme("blue")
        
        self.ssh_client = None
        self.is_running = False
        self.log_buffer = ""
        self.current_step = 0
        self.prompt_buffer = ""
        self.terminal_client = None
        
        self.create_widgets()
        self.init_tags()

    def create_widgets(self):
        """ Создание интерфейса приложения """
        self.tab_view = ctk.CTkTabview(self, width=960)
        self.tab_view.pack(padx=20, pady=(20, 10), fill="x")
        self.tab_view.add("Главная")
        self.tab_view.add("Настройки ZYXEL")

        main_tab_frame = self.tab_view.tab("Главная")
        main_wrapper = ctk.CTkFrame(main_tab_frame, fg_color="#333333", corner_radius=10, border_width=2, border_color="#555555")
        main_wrapper.pack(fill="x", padx=15, pady=10)
        main_wrapper.grid_columnconfigure((0, 1), weight=1, uniform="group1")

        # --- Левая колонка: Настройки подключения ---
        settings_frame = ctk.CTkFrame(main_wrapper, border_width=2, border_color="#777777") 
        settings_frame.grid(row=0, column=0, padx=(15, 10), pady=15, sticky="nsew")
        settings_frame.grid_columnconfigure(1, weight=1)
        
        # IP / Host
        ctk.CTkLabel(settings_frame, text="IP/Host:", font=ctk.CTkFont(family=self.FONT_FAMILY, size=12, weight="bold")).grid(row=0, column=0, padx=(15, 5), pady=10, sticky="w")
        self.host_entry = ctk.CTkEntry(settings_frame, width=120, placeholder_text="192.168.1.10", font=ctk.CTkFont(family=self.FONT_FAMILY, size=12))
        self.host_entry.insert(0, '192.168.1.10')
        self.host_entry.grid(row=0, column=1, padx=(5, 15), pady=10, sticky="ew")

        # User
        ctk.CTkLabel(settings_frame, text="User:", font=ctk.CTkFont(family=self.FONT_FAMILY, size=12, weight="bold")).grid(row=1, column=0, padx=(15, 5), pady=10, sticky="w")
        self.user_entry = ctk.CTkEntry(settings_frame, width=120, placeholder_text="root", font=ctk.CTkFont(family=self.FONT_FAMILY, size=12))
        self.user_entry.insert(0, 'root')
        self.user_entry.grid(row=1, column=1, padx=(5, 15), pady=10, sticky="ew")

        # Password
        ctk.CTkLabel(settings_frame, text="SSH Pass:", font=ctk.CTkFont(family=self.FONT_FAMILY, size=12, weight="bold")).grid(row=2, column=0, padx=(15, 5), pady=10, sticky="w")
        self.ssh_pass_entry = ctk.CTkEntry(settings_frame, width=120, placeholder_text="root", show='*', font=ctk.CTkFont(family=self.FONT_FAMILY, size=12))
        self.ssh_pass_entry.insert(0, 'root')
        self.ssh_pass_entry.grid(row=2, column=1, padx=(5, 15), pady=10, sticky="ew")
        
        # Кнопки левой колонки
        self.test_ssh_button = ctk.CTkButton(settings_frame, text="Тест SSH", command=self.test_ssh_threaded, fg_color="#666666", hover_color="#888888", font=ctk.CTkFont(family=self.FONT_FAMILY, size=12, weight="bold"))
        self.test_ssh_button.grid(row=3, column=0, columnspan=2, padx=15, pady=(10, 5), sticky="ew") 

        self.fw_toggle_button = ctk.CTkButton(settings_frame, text="Смена FW Huawei/Zyxel", command=self.toggle_fw_threaded, fg_color="#A020F0", hover_color="#8A2BE2", font=ctk.CTkFont(family=self.FONT_FAMILY, size=12, weight="bold"))
        self.fw_toggle_button.grid(row=4, column=0, columnspan=2, padx=15, pady=(5, 15), sticky="ew") 

        # --- Правая колонка: Прошивка и Восстановление ---
        main_control_frame = ctk.CTkFrame(main_wrapper, border_width=2, border_color="#777777") 
        main_control_frame.grid(row=0, column=1, padx=(10, 15), pady=15, sticky="nsew")

        # Логотип или заголовок
        try:
            logo_path = get_resource_path(LOGO_FILENAME)
            if os.path.exists(logo_path):
                img = Image.open(logo_path).convert("RGBA")
                self.zyxel_logo = ctk.CTkImage(light_image=img, dark_image=img, size=(250, 100))
                ctk.CTkLabel(main_control_frame, image=self.zyxel_logo, text="").pack(pady=(10, 5))
            else:
                ctk.CTkLabel(main_control_frame, text="HUAWEI FLASHER", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(20, 10))
        except:
            pass

        # Выбор файла прошивки
        ctk.CTkLabel(main_control_frame, text="Прошивка в Zyxel (.bin):", font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(5, 0), padx=20, anchor="w")
        file_select_frame = ctk.CTkFrame(main_control_frame, fg_color="transparent")
        file_select_frame.pack(fill="x", padx=20, pady=(0, 5))
        self.file_entry = ctk.CTkEntry(file_select_frame, placeholder_text="Выберите прошивку...")
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.browse_btn = ctk.CTkButton(file_select_frame, text="...", width=40, command=self.select_file)
        self.browse_btn.pack(side="right")

        self.start_button = ctk.CTkButton(main_control_frame, text="НАЧАТЬ ПРОШИВКУ", command=self.start_flashing_threaded, fg_color="#CC3333", hover_color="#EE5555", font=ctk.CTkFont(family=self.FONT_FAMILY, size=13, weight="bold"))
        self.start_button.pack(pady=(0, 15), padx=20, fill='x')

        ctk.CTkFrame(main_control_frame, height=2, fg_color="#555555").pack(fill="x", padx=10, pady=5) 

        # Выбор файла восстановления
        ctk.CTkLabel(main_control_frame, text="Восстановление uboot_env:", font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(5, 0), padx=20, anchor="w")
        restore_select_frame = ctk.CTkFrame(main_control_frame, fg_color="transparent")
        restore_select_frame.pack(fill="x", padx=20, pady=(0, 5))
        self.restore_file_entry = ctk.CTkEntry(restore_select_frame, placeholder_text="Файл бекапа env...")
        self.restore_file_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.browse_restore_btn = ctk.CTkButton(restore_select_frame, text="...", width=40, command=self.select_restore_file)
        self.browse_restore_btn.pack(side="right")

        self.restore_button = ctk.CTkButton(main_control_frame, text="ВОССТАНОВИТЬ ENV", command=self.start_restore_threaded, fg_color="#2E8B57", hover_color="#3CB371", font=ctk.CTkFont(family=self.FONT_FAMILY, size=13, weight="bold"))
        self.restore_button.pack(pady=(0, 15), padx=20, fill='x')
        
        # --- Вкладка: Настройки ZYXEL ---
        zyxel_tab_frame = self.tab_view.tab("Настройки ZYXEL")
        zyxel_settings_font = ctk.CTkFont(family=self.FONT_FAMILY, size=14, weight="bold")
        ctk.CTkLabel(zyxel_tab_frame, text="Подключение к Zyxel после прошивки (10.10.1.1):", font=zyxel_settings_font).pack(pady=(20, 5))
        
        self.terminal_button = ctk.CTkButton(zyxel_tab_frame, text="Открыть Терминал (Zyxel)", command=self.open_terminal_manual_threaded, fg_color="#4682B4", hover_color="#5F9EA0", width=200)
        self.terminal_button.pack(pady=(10, 20), padx=20)
        
        config_wrapper_frame = ctk.CTkFrame(zyxel_tab_frame, fg_color='transparent')
        config_wrapper_frame.pack(fill='x', padx=20, pady=(5, 5))
        config_wrapper_frame.grid_columnconfigure(0, weight=1)
        
        config_frame = ctk.CTkScrollableFrame(config_wrapper_frame, label_text="Быстрая смена параметров (SN / MAC / ID)", height=250, border_width=2, border_color="#777777") 
        config_frame.grid(row=0, column=0, sticky="nsew")
        config_frame.columnconfigure(0, weight=1) 
        config_frame.columnconfigure(1, weight=3) 
        config_frame.columnconfigure(2, weight=1) 

        row_count = 0
        def create_config_row(parent, label_text, placeholder, command_func, is_reboot=False):
            nonlocal row_count
            entry = None
            label = ctk.CTkLabel(parent, text=label_text, anchor='w')
            label.grid(row=row_count, column=0, padx=5, pady=5, sticky="w")
            if not is_reboot:
                entry = ctk.CTkEntry(parent, placeholder_text=placeholder)
                entry.grid(row=row_count, column=1, padx=5, pady=5, sticky="ew")
                button = ctk.CTkButton(parent, text="Применить", command=lambda: command_func(entry.get()), width=100)
                button.grid(row=row_count, column=2, padx=5, pady=5, sticky="e")
            else:
                button = ctk.CTkButton(parent, text="Перезагрузить Zyxel", command=command_func, width=150, fg_color='#CC3333', hover_color='#EE5555') 
                button.grid(row=row_count, column=1, columnspan=2, padx=5, pady=5, sticky="e") 
            row_count += 1
            return entry, button

        self.sn_entry, _ = create_config_row(config_frame, "Serial Number (SN):", "HWTC12345678", self.set_sn_threaded)
        self.ploam_entry, _ = create_config_row(config_frame, "PLOAM Password:", "Password", self.set_ploam_password_threaded)
        self.mac_entry, _ = create_config_row(config_frame, "MAC Address:", "00:11:22:33:44:55", self.set_pon_mac_threaded)
        self.equip_id_entry, _ = create_config_row(config_frame, "Equipment ID:", "ZYXEL-PMG3000", self.set_equipment_id_threaded)
        self.hw_ver_entry, _ = create_config_row(config_frame, "Hardware Version:", "V1.00", self.set_hardware_version_threaded)
        _, self.reboot_button = create_config_row(config_frame, "", "", self.reboot_zyxel_threaded, is_reboot=True)
        
        # --- Область логов ---
        log_frame = ctk.CTkFrame(self, fg_color="#1E1E1E", corner_radius=10, border_width=2, border_color="#777777")
        log_frame.pack(fill='both', expand=True, padx=20, pady=(0, 20))
        self.log_area = ctk.CTkTextbox(log_frame, wrap=tk.WORD, state='disabled', font=('Consolas', 11),
                                       text_color='light green', fg_color='black', corner_radius=5)
        self.log_area.pack(fill='both', expand=True, padx=8, pady=8) 

        self.log_message(f"🚀 Huawei Flash Tool v1.0 готов к работе.\n", "cyan", center=True)

    # --- Обработчики GUI ---
    def select_file(self):
        filename = filedialog.askopenfilename(title="Выберите файл прошивки", filetypes=(("Bin files", "*.bin"), ("All files", "*.*")))
        if filename:
            self.file_entry.delete(0, tk.END)
            self.file_entry.insert(0, filename)

    def select_restore_file(self):
        filename = filedialog.askopenfilename(title="Выберите файл бекапа env", filetypes=(("Bin files", "*.bin"), ("All files", "*.*")))
        if filename:
            self.restore_file_entry.delete(0, tk.END)
            self.restore_file_entry.insert(0, filename)

    def init_tags(self):
        """ Настройка цветов лога """
        self.log_area.tag_config('green', foreground='light green')
        self.log_area.tag_config('red', foreground='salmon') 
        self.log_area.tag_config('yellow', foreground='gold') 
        self.log_area.tag_config('blue', foreground='light blue')
        self.log_area.tag_config('magenta', foreground='violet')
        self.log_area.tag_config('gray', foreground='gray')
        self.log_area.tag_config('cyan', foreground='cyan')
        self.log_area.tag_config('center', justify='center')

    def log_message(self, message, tag_name='black', center=False):
        self.after(0, self._log_message_gui, message, tag_name, center)

    def _log_message_gui(self, message, tag_name, center):
        timestamped_message = f"[{time.strftime('%H:%M:%S')}] {message}"
        self.log_buffer += timestamped_message + "\n"
        self.log_area.configure(state='normal')
        tags = [tag_name]
        if center: tags.append('center')
        self.log_area.insert(tk.END, message + "\n", tuple(tags))
        self.log_area.see(tk.END)
        self.log_area.configure(state='disabled')

    # --- SSH Логика ---
    def test_ssh_threaded(self):
        if self.is_running: return
        Thread(target=self._run_ssh_test, args=(self.host_entry.get(), self.user_entry.get(), self.ssh_pass_entry.get())).start()

    def _run_ssh_test(self, host, user, password):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.log_message(f"⏳ Тест подключения к {host}...", "yellow")
            client.connect(host, username=user, password=password, timeout=5)
            self.log_message(f"✅ УСПЕХ: SSH подключен.", "green")
        except Exception as e:
            self.log_message(f"❌ ОШИБКА: {e}", "red")
        finally:
            client.close()

    def _create_ssh_client(self, server, user, password, device_name):
        self.log_message(f"⏳ SSH: Подключение к {device_name} ({server})...", "yellow")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(server, username=user, password=password, timeout=10)
            return client
        except Exception as e:
            self.log_message(f"❌ ОШИБКА: {e}", "red")
        return None

    # --- Процесс прошивки ---
    def start_flashing_threaded(self):
        if self.is_running: return
        host = self.host_entry.get()
        user = self.user_entry.get()
        password = self.ssh_pass_entry.get()
        firmware_path = self.file_entry.get()

        if not os.path.exists(firmware_path):
            self.log_message(f"❌ ОШИБКА: Файл прошивки не найден!", "red")
            return

        confirm = simpledialog.askstring("Подтверждение", "Напишите 'ДА' для начала прошивки:", parent=self)
        if confirm != 'ДА': return

        self.is_running = True
        self._prepare_gui_for_start("Прошивка...")
        Thread(target=self._flashing_process, args=(host, user, password, firmware_path)).start()

    def start_restore_threaded(self):
        if self.is_running: return
        host = self.host_entry.get()
        user = self.user_entry.get()
        password = self.ssh_pass_entry.get()
        env_path = self.restore_file_entry.get()

        if not os.path.exists(env_path):
            self.log_message(f"❌ ОШИБКА: Файл бекапа не найден!", "red")
            return

        confirm = simpledialog.askstring("Подтверждение", "Напишите 'ДА' для восстановления ENV:", parent=self)
        if confirm != 'ДА': return

        self.is_running = True
        self._prepare_gui_for_start("Восстановление...")
        Thread(target=self._restore_process, args=(host, user, password, env_path)).start()

    def _prepare_gui_for_start(self, btn_text):
        self.start_button.configure(state='disabled')
        self.restore_button.configure(state='disabled')
        self.log_area.configure(state='normal')
        self.log_area.delete('1.0', tk.END)
        self.log_area.configure(state='disabled')

    def _restore_process(self, host, user, password, env_path):
        success = False
        try:
            self.ssh_client = self._create_ssh_client(host, user, password, DEVICE_NAME)
            if not self.ssh_client: return

            self.log_message("--- ЭТАП 1: ЗАГРУЗКА БЕКАПА ---", "blue")
            if not self._scp_put(env_path, '/tmp/env_restore.bin', "файла env"): return

            self.log_message("--- ЭТАП 2: ЗАПИСЬ И ПЕРЕЗАГРУЗКА ---", "blue")
            try:
                self._run_command('mtd -e uboot_env write /tmp/env_restore.bin uboot_env', "ЗАПИСЬ ENV")
                self._run_command('reboot', "ПЕРЕЗАГРУЗКА")
            except:
                self.log_message("✅ Устройство перезагружается.", "green")

            success = True
        finally:
            if self.ssh_client:
                try: self.ssh_client.close()
                except: pass
            self.is_running = False
            self.start_button.configure(state='normal')
            self.restore_button.configure(state='normal')
            if success: self.log_message("\n🎉 ВОССТАНОВЛЕНИЕ ЗАВЕРШЕНО!", "magenta", center=True)

    def _flashing_process(self, host, user, password, firmware_path):
        success = False
        try:
            self.ssh_client = self._create_ssh_client(host, user, password, DEVICE_NAME)
            if not self.ssh_client: return

            self.log_message("--- ЭТАП 1: БЕКАП ---", "blue")
            if not os.path.exists(BACKUP_DIR): os.makedirs(BACKUP_DIR)
            self._run_command('cat /dev/mtd1 > /tmp/env.bin', "создание дампа env")
            self._scp_get('/tmp/env.bin', os.path.join(BACKUP_DIR, 'env.bin'), "скачивание бекапа")

            self.log_message("--- ЭТАП 2: НАСТРОЙКА ---", "blue")
            local_cm1 = "cm1_temp.sh"
            with open(local_cm1, "w", newline='\n') as f: f.write(CM1_CONTENT)
            if not self._scp_put(local_cm1, '/tmp/cm1.sh', "скрипта настройки"): 
                if os.path.exists(local_cm1): os.remove(local_cm1)
                return
            if os.path.exists(local_cm1): os.remove(local_cm1)
            self._run_command('chmod +x /tmp/cm1.sh && sh /tmp/cm1.sh && rm /tmp/cm1.sh', "выполнение настройки")

            self.log_message("--- ЭТАП 3: ЗАГРУЗКА ---", "blue")
            if not self._scp_put(firmware_path, '/tmp/firmware.bin', "загрузка прошивки"): return

            self.log_message("--- ЭТАП 4: ПРОШИВКА И REBOOT ---", "blue")
            local_cm2 = "cm2_temp.sh"
            with open(local_cm2, "w", newline='\n') as f: f.write(CM2_CONTENT)
            if not self._scp_put(local_cm2, '/tmp/cm2.sh', "скрипта записи"):
                if os.path.exists(local_cm2): os.remove(local_cm2)
                return
            if os.path.exists(local_cm2): os.remove(local_cm2)
            self._run_command('chmod +x /tmp/cm2.sh', "права на скрипт")
            try:
                self._run_command('sh /tmp/cm2.sh', "ЗАПИСЬ И ПЕРЕЗАГРУЗКА")
            except:
                self.log_message("✅ Устройство перезагружается.", "green")

            success = True
        finally:
            if self.ssh_client:
                try: self.ssh_client.close()
                except: pass
            self.is_running = False
            self.start_button.configure(state='normal')
            self.restore_button.configure(state='normal')
            if success: self.log_message("\n🎉 ПРОЦЕСС ЗАВЕРШЕН УСПЕШНО!", "magenta", center=True)

    def _scp_put(self, local, remote, desc):
        self.log_message(f"⏳ Отправка {desc}...", "yellow")
        try:
            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.put(local, remote)
            self.log_message("✅ Загружено.", "green")
            return True
        except Exception as e:
            self.log_message(f"❌ Ошибка SCP: {e}", "red")
            return False

    def _scp_get(self, remote, local, desc):
        try:
            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.get(remote, local)
            self.log_message(f"✅ Бекап сохранен: {local}", "green")
            return True
        except Exception as e:
            self.log_message(f"❌ Ошибка SCP Get: {e}", "red")
            return False

    def _run_command(self, cmd, desc):
        self.log_message(f"⏳ {desc}...", "yellow")
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(cmd, timeout=300)
            status = stdout.channel.recv_exit_status()
            if status == 0: 
                self.log_message("✅ Выполнено.", "green")
                return True
            else:
                self.log_message(f"⚠️ Ошибка (код {status}): {stderr.read().decode()}", "red")
                return False
        except Exception as e:
            self.log_message(f"❌ Exception: {e}", "red")
            return False

    # --- Переключение режимов (Huawei/Zyxel) ---
    def toggle_fw_threaded(self):
        if self.is_running: return
        self.is_running = True
        Thread(target=self._run_fw_toggle).start()

    def _run_fw_toggle(self):
        try:
            self.log_message("\n--- СМЕНА ПРОШИВКИ ---", "magenta")
            # Проверка подключения к Huawei
            client = self._create_ssh_client(HUAWEI_IP, HUAWEI_USER, HUAWEI_PASS, "Huawei")
            if client:
                self._execute_toggle_commands(client, HUAWEI_SWITCH_CMDS, "Huawei")
                return
            # Проверка подключения к Zyxel
            client = self._create_ssh_client(NEW_ROUTER_IP, NEW_ROUTER_USER, NEW_ROUTER_PASS, "Zyxel")
            if client:
                self._execute_toggle_commands(client, ZYXEL_SWITCH_CMDS, "Zyxel")
                return
            self.log_message("❌ Не удалось подключиться ни к одному режиму.", "red")
        finally:
            self.is_running = False

    def _execute_toggle_commands(self, client, commands, mode):
        self.log_message(f"✅ Подключено к {mode}. Выполняем смену...", "green")
        try:
            if mode == "Zyxel":
                chan = client.invoke_shell()
                time.sleep(1)
                for cmd in commands:
                    chan.send(cmd + '\n')
                    time.sleep(CRITICAL_COMMAND_TIMEOUT if cmd in ('twmanu','linuxshell') else SWITCH_COMMAND_TIMEOUT)
            else:
                for cmd in commands:
                    client.exec_command(cmd)
                    time.sleep(1)
            self.log_message("🎉 Команды отправлены. Перезагрузка...", "magenta")
        except Exception as e:
            self.log_message(f"Ошибка: {e}", "red")
        finally:
            client.close()

    # --- Настройки Zyxel ---
    def _execute_zyxel_config(self, commands, desc):
        """ Выполнение последовательности команд в оболочке Zyxel """
        try:
            self.log_message(f"--- Настройка: {desc} ---", "blue")
            client = self._create_ssh_client(NEW_ROUTER_IP, NEW_ROUTER_USER, NEW_ROUTER_PASS, "Zyxel")
            if not client: return

            chan = client.invoke_shell()
            time.sleep(1)
            
            # Вход в twmanu
            chan.send('twmanu\n')
            time.sleep(0.5)
            chan.send('twmanu\n')
            time.sleep(0.5)

            for cmd in commands:
                self.log_message(f"CMD: {cmd}", "gray")
                chan.send(cmd + '\n')
                time.sleep(1) 
            
            time.sleep(1)
            client.close()
            self.log_message(f"✅ {desc} завершено.", "green")
        except Exception as e:
            self.log_message(f"❌ Ошибка: {e}", "red")

    def open_terminal_manual_threaded(self):
        """ Открытие внешнего терминала SSH """
        if sys.platform == 'win32':
            try:
                os.system(f"start cmd /k ssh {NEW_ROUTER_USER}@{NEW_ROUTER_IP}")
                self.log_message("✅ Терминал открыт в новом окне.", "green")
            except Exception as e:
                self.log_message(f"❌ Ошибка открытия терминала: {e}", "red")
        else:
            self.log_message("Терминал поддерживается только на Windows", "red")

    def set_sn_threaded(self, v):
        if not v: return
        cmds = [
            'manufactory',
            f'set sn {v}',
            'exit',
            'hal',
            f'set sn {v}',
            'exit'
        ]
        Thread(target=self._execute_zyxel_config, args=(cmds, f"Смена SN на {v}")).start()

    def set_ploam_password_threaded(self, v):
        if not v: return
        cmds = [
            'hal',
            f'set password {v}',
            'exit'
        ]
        Thread(target=self._execute_zyxel_config, args=(cmds, f"Смена PLOAM на {v}")).start()

    def set_pon_mac_threaded(self, v):
        if not v: return
        cmds = [
            'linuxshell',
            f'fw_setenv ethaddr {v}',
            'exit',
            'hal', 
            f'set mac {v}',
            'exit'
        ]
        Thread(target=self._execute_zyxel_config, args=(cmds, f"Смена MAC на {v}")).start()

    def set_equipment_id_threaded(self, v):
        if not v: return
        cmds = [
            'manufactory', 
            f'set equipment id {v}',
            'exit',
            'omci',
            f'equipment id {v}',
            'exit'
        ]
        Thread(target=self._execute_zyxel_config, args=(cmds, f"Смена Equipment ID на {v}")).start()

    def set_hardware_version_threaded(self, v):
        if not v: return
        cmds = [
            'manufactory',
            f'set hardware version {v}',
            'exit'
        ]
        Thread(target=self._execute_zyxel_config, args=(cmds, f"Смена HW Ver на {v}")).start()

    def reboot_zyxel_threaded(self):
        cmds = [
            'linuxshell',
            'system',
            'reboot'
        ]
        Thread(target=self._execute_zyxel_config, args=(cmds, "Перезагрузка Zyxel")).start()

if __name__ == '__main__':
    app = App()
    app.mainloop()
