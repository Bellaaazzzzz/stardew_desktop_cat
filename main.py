"""
Stardew Valley Desktop Cat - Final Release Version
"""
import sys
import os
import random
import math
from pathlib import Path
from datetime import datetime
from enum import Enum, auto
import traceback
import logging
import json
import ctypes

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('desktop_cat_debug.log', encoding='utf-8'),
        #logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

from PyQt5.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QAction, QWidget,
    QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton, QHBoxLayout
)
from PyQt5.QtGui import QIcon, QPixmap, QImage, QTransform, QPainter, QColor, QBrush
from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QRectF
from PIL import Image


def resource_path(relative_path):
    """获取资源的绝对路径 (兼容开发环境与PyInstaller打包后的环境)"""
    try:
        # PyInstaller 创建临时文件夹 _MEIPASS
        if hasattr(sys, '_MEIPASS'):
            return Path(sys._MEIPASS) / relative_path
    except Exception:
        pass
    return Path(__file__).parent / relative_path

def get_config_path():
    """获取配置文件的存储路径 (存放在用户AppData目录下，避免exe目录权限问题)"""
    app_data = os.getenv('APPDATA')
    if app_data:
        config_dir = Path(app_data) / 'DesktopCat'
    else:
        config_dir = Path(os.getcwd()) / 'DesktopCatConfig'
    
    if not config_dir.exists():
        config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / 'config.json'


class CatState(Enum):
    WALK = auto(); SIT = auto(); SLEEP = auto(); FLOP = auto()
    LICK = auto(); LEAP = auto(); BLINK = auto(); STAND_UP = auto()

class Direction(Enum):
    DOWN = 0; LEFT = 1; RIGHT = 2; UP = 3


class SettingsDialog(QDialog):
    """设置窗口"""
    def __init__(self, current_breed, assets_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("桌宠设置")
        self.setFixedSize(250, 120)
        self.assets_dir = assets_dir
        self.selected_breed = current_breed
        
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("选择猫猫样式:"))
        self.combo = QComboBox()
        
        # 扫描资源目录，自动识别有多少只猫
        if assets_dir.exists():
            files = list(assets_dir.glob('cat*.png'))
            for f in sorted(files):
                name = f.stem
                if name == 'cat':
                    breed_id = 0
                    display_name = "默认橘猫"
                else:
                    try:
                        breed_id = int(name.replace('cat', ''))
                        display_name = f"猫猫 {breed_id}"
                    except ValueError:
                        continue
                
                self.combo.addItem(display_name, breed_id)
                if breed_id == current_breed:
                    self.combo.setCurrentText(display_name)
                    
        layout.addWidget(self.combo)
        
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def get_selected_breed(self):
        return self.combo.currentData()


class CatBehavior:
    """猫猫行为逻辑"""
    WALK_FRAMES = { Direction.DOWN: [0, 1, 2, 3], Direction.RIGHT: [4, 5, 6, 7], Direction.UP: [8, 9, 10, 11], Direction.LEFT: [12, 13, 14, 15] }
    SIT_FRAMES = [16, 17, 18]; SIT_IDLE = 18; SIT_BLINK = 19
    LICK_FRAMES = [19, 20, 21, 22, 23]
    FLOP_FRAMES = [24, 25, 26, 27]; FLOP_IDLE = 27
    SLEEP_FRAMES = [28, 29]; LEAP_FRAMES = [30, 31]
    FLOP_MIN_DURATION = 10000; DEFAULT_FRAME_DURATION = 700; DEFAULT_FRAME_RANDOM = 400
    
    def __init__(self):
        self.state = CatState.SIT; self.direction = Direction.DOWN; self.current_frame = 0; self.flip = False
        self.animation_frames = []; self.animation_index = 0; self.animation_timer = 0
        self.frame_duration = self.DEFAULT_FRAME_DURATION; self.frame_duration_random = self.DEFAULT_FRAME_RANDOM
        self.is_moving = False; self.is_leaping = False; self.flop_min_duration = 10000; self.sit_cooldown = 0  
        
    def get_frame_index(self):
        return self.animation_frames[self.animation_index] if self.animation_frames else self.current_frame
    
    def update(self, dt, is_night, config):
        try:
            self.animation_timer += dt
            if self.animation_frames:
                base_duration = self.flop_frame_config["base"] if (hasattr(self, 'flop_frame_config') and self.state == CatState.FLOP) else self.frame_duration
                current_duration = max(50, base_duration + random.randint(-self.frame_duration_random, self.frame_duration_random))
                if self.state == CatState.FLOP and self.animation_index == len(self.animation_frames) - 1:
                    current_duration = random.randint(self.flop_frame_config["last_frame_min"], self.flop_frame_config["last_frame_max"])
                if self.animation_timer >= current_duration:
                    self.animation_timer = 0; self.animation_index += 1
                    if self.animation_index >= len(self.animation_frames):
                        if self.state in [CatState.WALK, CatState.SLEEP]: self.animation_index = 0
                        elif self.state == CatState.LEAP: self.animation_index = len(self.animation_frames) - 1
                        else: self.on_animation_end()
            if self.is_moving and hasattr(self, 'move_timer'):
                self.move_timer += dt; self.update_movement(dt)
                if self.move_timer >= self.move_duration:
                    self.is_moving = False; delattr(self, 'move_timer'); self.start_sit()
            if self.state == CatState.FLOP and hasattr(self, 'flop_start_time'): self.flop_start_time += dt
            if self.state == CatState.SIT and self.sit_cooldown > 0: self.sit_cooldown -= dt
            self.think(is_night, config)
        except Exception as e: logger.error(f"Error in behavior update: {e}")

    def think(self, is_night, config):
        try:
            rand = random.random(); behavior = config.get('behavior', {})
            if self.state == CatState.SLEEP:
                if not is_night and rand < 0.005: self.wake_up()
                return
            if is_night and self.state != CatState.SLEEP and rand < behavior.get('sleepChance', 0.005) * 3: self.start_sleep(); return
            if self.state == CatState.SIT:
                if self.sit_cooldown > 0: return
                w, f, l, b = behavior.get('walkChance', 0.02), behavior.get('flopChance', 0.01), behavior.get('lickChance', 0.005), 0.01
                if rand < w: self.start_walk()
                elif rand < w + f: self.start_flop()
                elif rand < w + f + l: self.start_lick()
                elif rand < w + f + l + b: self.start_blink()
            elif self.state == CatState.FLOP and hasattr(self, 'flop_start_time') and self.flop_start_time >= self.flop_min_duration:
                if rand < 0.008: self.start_sit(); delattr(self, 'flop_start_time'); delattr(self, 'flop_frame_config')
        except Exception as e: logger.error(f"Error in think: {e}")

    def start_walk(self):
        self.state = CatState.WALK; self.is_moving = True; self.direction = random.choice([Direction.LEFT, Direction.RIGHT]); self.flip = False
        self.animation_frames = list(self.WALK_FRAMES[self.direction]); self.animation_index = 0
        self.move_timer = 0; self.move_duration = random.randint(5000, 10000); self.walk_speed = 2.0; self.frame_duration = random.randint(80, 150)
    def start_sit(self):
        self.state = CatState.SIT; self.is_moving = False; self.animation_frames = self.SIT_FRAMES[:]; self.animation_index = 0
        self.current_frame = self.SIT_IDLE; self.direction = Direction.DOWN; self.flip = random.random() < 0.5; self.sit_cooldown = random.randint(2000, 10000)
    def start_sleep(self):
        self.state = CatState.SLEEP; self.is_moving = False; self.animation_frames = self.SLEEP_FRAMES[:]; self.animation_index = 0; self.flip = random.random() < 0.5
    def start_flop(self):
        self.state = CatState.FLOP; self.is_moving = False; self.direction = random.choice([Direction.LEFT, Direction.RIGHT]); self.flip = self.direction == Direction.LEFT
        self.animation_frames = self.FLOP_FRAMES[:]; self.animation_index = 0; self.flop_start_time = 0
        self.flop_frame_config = {"base": 100, "random": 50, "last_frame_min": 500, "last_frame_max": 800}
    def start_lick(self):
        self.state = CatState.LICK; self.is_moving = False
        self.animation_frames = self.LICK_FRAMES[:] if random.random() < 0.5 else self.LICK_FRAMES[:] + [self.LICK_FRAMES[-1]] + self.LICK_FRAMES[:]
        self.animation_index = 0; self.flip = random.random() < 0.5
    def start_blink(self):
        self.state = CatState.BLINK; self.animation_frames = [self.SIT_IDLE, self.SIT_BLINK, self.SIT_IDLE] * 2; self.animation_index = 0
    def start_leap(self):
        self.state = CatState.LEAP; self.direction = random.choice([Direction.LEFT, Direction.RIGHT]); self.flip = self.direction == Direction.LEFT
        self.animation_frames = self.LEAP_FRAMES[:]; self.animation_index = 0; self.is_leaping = True
        self.leap_vy = -8.0; self.leap_vx = -6.0 if self.direction == Direction.LEFT else 6.0; self.leap_total_dy = 0; self.frame_duration = random.randint(60, 120)

    def update_movement(self, dt):
        if not hasattr(self, 'move_timer'): return 0, 0
        speed = getattr(self, 'walk_speed', 2.0); dx, dy = 0, 0
        if self.direction == Direction.LEFT: dx = -speed
        elif self.direction == Direction.RIGHT: dx = speed
        return dx, dy
    def update_leap(self, dt):
        if not hasattr(self, 'leap_vy'): return 0, 0
        dx = self.leap_vx; self.leap_vy += 0.7; self.leap_total_dy += self.leap_vy
        if self.leap_total_dy >= 0:
            self.leap_total_dy = 0; self.is_leaping = False
            for attr in ['leap_vy', 'leap_vx', 'leap_total_dy']:
                if hasattr(self, attr): delattr(self, attr)
            self.start_sit()
        return dx, 0
    def on_animation_end(self):
        self.animation_frames = []; self.animation_index = 0
        if self.state == CatState.FLOP: self.current_frame = self.FLOP_IDLE
        elif self.state in [CatState.BLINK, CatState.LICK]: self.state = CatState.SIT; self.current_frame = self.SIT_IDLE
    def wake_up(self): self.state = CatState.SIT; self.animation_frames = []; self.current_frame = self.SIT_IDLE
    def on_click(self):
        if self.state == CatState.SLEEP: self.wake_up()
        elif self.state in [CatState.SIT, CatState.FLOP]:
            r = random.random()
            if r < 0.2: self.start_blink()
            elif r < 0.4: self.start_lick()
            else: self.start_leap()


class CatWidget(QWidget):
    """猫猫窗口组件"""
    def __init__(self, sprite_path, config):
        super().__init__()
        self.config = config; self.behavior = CatBehavior()
        self.sprite_sheet = Image.open(sprite_path); self.frame_size = 32; self.scale = 3
        self.frames = self.load_frames()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_ShowWithoutActivating)
        window_size = self.frame_size * self.scale; self.setFixedSize(window_size, window_size)
        self.position = QPoint(100, 100); self.move(self.position)
        self.update_available_area()
        self.timer = QTimer(); self.timer.timeout.connect(self.update_frame); self.timer.start(50)
        self.current_pixmap = None; self.update_display()

        self.show()
        
        # ====== 新增：底层硬置顶保活机制 ======
        self.force_topmost()  # 启动时先强制置顶一次
        self.topmost_timer = QTimer()
        self.topmost_timer.timeout.connect(self.force_topmost)
        self.topmost_timer.start(60000)  # 每5秒默默刷新一次置顶状态
    
    def force_topmost(self):
        """使用Windows底层API强制置顶，无视其他软件的普通置顶"""
        if sys.platform == 'win32':  # 仅在Windows下执行，不影响Mac用户
            try:
                hwnd = int(self.winId())
                # 参数说明：
                # -1 代表 HWND_TOPMOST (置顶)
                # 0x0002 代表 SWP_NOMOVE (不改变位置)
                # 0x0001 代表 SWP_NOSIZE (不改变大小)
                # 0x0010 代表 SWP_NOACTIVATE (极其重要：不抢走键盘输入焦点)
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)
            except Exception:
                pass  # 静默失败，防止在某些特殊环境报错

    def load_frames(self):
        frames = {}; sheet_width, sheet_height = self.sprite_sheet.size
        cols = sheet_width // self.frame_size; rows = sheet_height // self.frame_size
        for row in range(rows):
            for col in range(cols):
                frame_idx = row * cols + col
                frame = self.sprite_sheet.crop((col*self.frame_size, row*self.frame_size, (col+1)*self.frame_size, (row+1)*self.frame_size))
                scaled_size = self.frame_size * self.scale; frame = frame.resize((scaled_size, scaled_size), Image.NEAREST)
                data = frame.convert('RGBA').tobytes('raw', 'BGRA')
                qimg = QImage(data, scaled_size, scaled_size, QImage.Format_ARGB32)
                frames[frame_idx] = QPixmap.fromImage(qimg)
        return frames

    def update_available_area(self):
        desktop = QApplication.desktop(); self.screen_rect = desktop.screenGeometry(desktop.primaryScreen())

    def update_frame(self):
        dt = 50; self.behavior.update(dt, self.is_night(), self.config); dx = 0
        if self.behavior.is_moving and self.behavior.state == CatState.WALK: dx, _ = self.behavior.update_movement(dt)
        if self.behavior.is_leaping: leap_dx, _ = self.behavior.update_leap(dt); dx += leap_dx
        if dx != 0:
            new_x = self.position.x() + dx
            desktop = QApplication.desktop()
            screen_num = desktop.screenNumber(self)
            screen = desktop.screenGeometry(screen_num)
            
            scaled_size = self.frame_size * self.scale
            margin = 50  # 离边缘的距离
                
            min_x = screen.left() + margin
            max_x = screen.right() - scaled_size - margin
            
            available_rect = desktop.availableGeometry(screen_num)
            ground_y = available_rect.bottom() - scaled_size + 8

            new_x = max(min_x, min(max_x, new_x))
            self.position.setX(int(max(min_x, min(max_x, new_x))))
            self.position.setY(ground_y)
            self.move(self.position)
        self.update_display()

    def update_display(self):
        frame_idx = self.behavior.get_frame_index()
        if frame_idx in self.frames:
            self.current_pixmap = self.frames[frame_idx]
            if self.behavior.flip:
                transform = QTransform(); transform.scale(-1, 1); self.current_pixmap = self.current_pixmap.transformed(transform)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.current_pixmap:
            if self.behavior.is_leaping:
                visual_dy = getattr(self.behavior, 'leap_total_dy', 0)
                shadow_scale = max(0.3, 1.0 - abs(visual_dy) / 100.0)
                shadow_width = int(60 * shadow_scale); shadow_height = int(15 * shadow_scale)
                painter.setOpacity(0.4 * shadow_scale); painter.setBrush(QBrush(QColor(0, 0, 0))); painter.setPen(Qt.NoPen)
                painter.drawEllipse(QRectF((self.width() - shadow_width) // 2, self.height() - shadow_height - 5, shadow_width, shadow_height))
                painter.setOpacity(1.0); painter.drawPixmap(0, int(visual_dy), self.current_pixmap)
            else: painter.drawPixmap(0, 0, self.current_pixmap)
        else: painter.setBrush(Qt.red); painter.drawEllipse(5, 5, 22, 22)

    def is_night(self):
        hour = datetime.now().hour; time_config = self.config.get('time', {})
        return hour >= time_config.get('nightStart', 20) or hour < time_config.get('nightEnd', 6)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self.drag_position = event.globalPos() - self.pos(); self.behavior.on_click(); self.update_display()
    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton: self.move(event.globalPos() - self.drag_position); self.position = self.pos()
    def show_cat(self): self.show()
    def hide_cat(self): self.hide()


class DesktopCat:
    """桌宠主程序"""
    def __init__(self):
        self.app = QApplication(sys.argv); self.app.setQuitOnLastWindowClosed(False)
        self.config = self.load_config()
        
        # 获取资源目录 (重要：打包后指向内部，开发环境指向本地assets)
        self.assets_dir = resource_path('assets/cat')
        
        # 初始化猫猫
        self.cat_widget = None
        self.init_cat_widget()
        self.set_initial_position()
        self.create_tray()

    def init_cat_widget(self):
        """初始化或重置猫猫窗口"""
        breed = self.config.get('breed', 0)
        if breed == 0: sprite_path = self.assets_dir / 'cat.png'
        else: sprite_path = self.assets_dir / f'cat{breed}.png'
        
        if not sprite_path.exists():
            default_path = self.assets_dir / 'cat.png'
            if default_path.exists(): sprite_path = default_path
            else: logger.error(f"找不到精灵图: {sprite_path}"); return
            
        self.cat_widget = CatWidget(str(sprite_path), self.config)

    def load_config(self):
        try:
            # 1. 优先读取代码同级的 config.json（方便开发调试）
            local_config_path = Path(__file__).parent / 'config.json'
            if local_config_path.exists():
                return json.load(open(local_config_path, 'r', encoding='utf-8'))
                
            # 2. 如果本地没有，再读取用户系统 AppData 的配置（打包后使用）
            config_path = get_config_path()
            if config_path.exists():
                return json.load(open(config_path, 'r', encoding='utf-8'))
        except Exception as e: 
            logger.error(f"加载配置失败: {e}")
        return {}

    def save_config(self):
        try:
            with open(get_config_path(), 'w', encoding='utf-8') as f: json.dump(self.config, f, ensure_ascii=False, indent=4)
        except Exception as e: logger.error(f"保存配置失败: {e}")

    def set_initial_position(self):
        if not self.cat_widget: return
        desktop = QApplication.desktop()
        screen_num = desktop.primaryScreen()
        screen = desktop.screenGeometry(screen_num)
        
        available_rect = desktop.availableGeometry(screen_num)
        
        scaled_size = 32 * 3
        x = screen.center().x() - scaled_size // 2
        y = available_rect.bottom() - scaled_size + 8
        self.cat_widget.position = QPoint(x, y)
        self.cat_widget.move(x, y)

    def create_tray(self):
        tray_icon = QSystemTrayIcon(self.app)
        pixmap = QPixmap(32, 32); pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap); painter.setBrush(QColor(255, 165, 0)); painter.drawEllipse(4, 4, 24, 24); painter.end()
        tray_icon.setIcon(QIcon(pixmap)); tray_icon.setToolTip("Stardew Cat")
        
        menu = QMenu()
        menu.addAction("显示", self.cat_widget.show_cat)
        menu.addAction("隐藏", self.cat_widget.hide_cat)
        menu.addSeparator()
        menu.addAction("设置", self.open_settings)  # 新增设置按钮
        menu.addSeparator()
        menu.addAction("退出", self.app.quit)
        
        tray_icon.setContextMenu(menu); tray_icon.show()
        self.tray_icon = tray_icon

    def open_settings(self):
        """打开设置窗口"""
        dialog = SettingsDialog(self.config.get('breed', 0), self.assets_dir)
        if dialog.exec_() == QDialog.Accepted:
            new_breed = dialog.get_selected_breed()
            if new_breed != self.config.get('breed', 0):
                self.reload_cat(new_breed)

    def reload_cat(self, new_breed):
        """热重载猫猫：销毁旧窗口，加载新贴图"""
        self.config['breed'] = new_breed
        self.save_config()
        
        self.cat_widget.close()
        self.cat_widget.deleteLater() # 安全释放 PyQt 内存
        
        self.init_cat_widget()
        self.set_initial_position()

    def run(self):
        return self.app.exec_()

if __name__ == '__main__':
    cat = DesktopCat()
    sys.exit(cat.run())
