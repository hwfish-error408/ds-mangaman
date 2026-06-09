import sys
import os
import subprocess
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton, QTextEdit, QMessageBox, QFileDialog
from PyQt6.QtCore import Qt

def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DS-Mangaman ROM Builder")
        self.setFixedSize(520, 380)

        # Main layout
        main_widget = QWidget()
        layout = QVBoxLayout()

        # Button 1: Upload / Import Manga
        self.btn_upload = QPushButton("Import Manga / Chapters")
        self.btn_upload.clicked.connect(self.on_upload_manga)
        layout.addWidget(self.btn_upload)

        # Button 2: Build ROM
        self.btn_build = QPushButton("Build NDS ROM")
        self.btn_build.clicked.connect(self.on_build_rom)
        layout.addWidget(self.btn_build)

        # Status & Log Console
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Console logs and system status will be displayed here...")
        layout.addWidget(self.log_output)

        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)

        # Trigger environment validation on startup
        self.check_and_setup_environment()

    def validate_and_inject_paths(self, devkitpro_root):
        """
        Validates the chosen/detected devkitPro root folder.
        If valid, dynamically binds it to the current running process across platforms.
        """
        if not devkitpro_root or not os.path.exists(devkitpro_root):
            return False

        # Define cross-platform binary names based on OS
        gcc_name = "arm-none-eabi-gcc.exe" if sys.platform == "win32" else "arm-none-eabi-gcc"
        ndstool_name = "ndstool.exe" if sys.platform == "win32" else "ndstool"

        # Verify the presence of essential cross-platform components
        devkitarm_dir = os.path.join(devkitpro_root, "devkitARM")
        compiler_path = os.path.join(devkitarm_dir, "bin", gcc_name)
        tools_bin_dir = os.path.join(devkitpro_root, "tools", "bin")

        if os.path.exists(compiler_path):
            # Format paths safely using forward slashes for cross-platform tools / Makefiles
            clean_pro = os.path.abspath(devkitpro_root).replace("\\", "/")
            clean_arm = os.path.abspath(devkitarm_dir).replace("\\", "/")
            
            # Force inject into process environment variables
            os.environ["DEVKITPRO"] = clean_pro
            os.environ["DEVKITARM"] = clean_arm
            
            # Add compiler and tools to system PATH so 'make' can execute them natively
            compiler_bin_dir = os.path.join(devkitarm_dir, "bin")
            path_sep = ";" if sys.platform == "win32" else ":"
            current_path = os.environ.get("PATH", "")
            
            os.environ["PATH"] = f"{compiler_bin_dir}{path_sep}{tools_bin_dir}{path_sep}{current_path}"
            
            self.log_output.append(f"[System] Environment successfully verified & injected.")
            self.log_output.append(f"[System] DEVKITPRO: {os.environ['DEVKITPRO']}")
            return True

        return False

    def check_and_setup_environment(self):
        """Scans for devkitPro. If not found, prompts the user to select the folder."""
        # 1. Gather potential default paths across different operating systems
        possible_roots = []
        
        # Pull from existing environment variable if available
        if "DEVKITPRO" in os.environ:
            possible_roots.append(os.environ["DEVKITPRO"].strip('"\''))

        # Add platform-specific standard defaults
        if sys.platform == "win32":
            possible_roots.extend(["C:/devkitpro", "D:/devkitpro", "C:/devkitPro", "D:/devkitpro"])
        else:
            # macOS / Linux default paths
            possible_roots.extend(["/opt/devkitpro", "/opt/devkitPro", os.path.expanduser("~/devkitpro"), os.path.expanduser("~/devkitPro")])

        # 2. Test default paths
        for root in possible_roots:
            if self.validate_and_inject_paths(root):
                return

        # 3. If all automated checks fail, fallback to Manual Folder Selection
        self.log_output.append("[System] devkitPro toolchain not found in default locations.")
        self.prompt_user_for_devkit_path()

    def prompt_user_for_devkit_path(self):
        """Opens a native directory dialog asking the user to manually select the devkitPro folder."""
        QMessageBox.information(
            self,
            "devkitPro Required",
            "The devkitPro compiler toolchain was not found.\n\n"
            "Please click OK to select your 'devkitpro' installation root folder manually."
        )

        selected_dir = QFileDialog.getExistingDirectory(
            self, 
            "Select devkitPro Root Directory",
            "C:/" if sys.platform == "win32" else os.path.expanduser("~")
        )

        if selected_dir:
            if self.validate_and_inject_paths(selected_dir):
                self.log_output.append("[System] Manual path registration complete. Ready.")
            else:
                self.log_output.append("[Error] The selected folder does not contain a valid devkitARM compiler.")
                # Loop back if they selected a wrong folder
                self.prompt_user_for_devkit_path()
        else:
            self.log_output.append("[Warning] No folder selected. ROM generation functions will be unavailable.")

    def on_upload_manga(self):
        """Placeholder for uploading manga chapters"""
        self.log_output.append("[Action] 'Import Manga / Chapters' clicked (Logic pending...)")

    def on_build_rom(self):
        """Placeholder for invoking compilation toolchain"""
        self.log_output.append("[Action] 'Build NDS ROM' clicked (Logic pending...)")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())