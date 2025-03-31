#!/usr/bin/env python3

import os
import subprocess
import threading
import sys
import json
import shutil  # <<< ADDED (for shutil.which)

import gi

from python_runner.version import VERSION

gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "3.0")

from gi.repository import Gtk, Gio, Pango, GtkSource, Gdk, GLib

# --- Constants ---
APP_ID = "com.example.python-runner"
# SETTINGS_SCHEMA = APP_ID # <<< REMOVED (No longer using GSettings schema)
INITIAL_WIDTH, INITIAL_HEIGHT = 700, 500
DEFAULT_STYLE_SCHEME = "oblivion"
STATUS_MESSAGE_TIMEOUT_MS = 2000  # 2 seconds (Used for non-execution messages)
DEFAULT_TAB_SIZE = 4
DEFAULT_TRANSLATE_TABS = True
DEFAULT_DRAW_WHITESPACES = False  # <<< ADDED explicit default

# Settings Keys (reused for JSON)
SETTING_DRAW_WHITESPACES = "draw-whitespaces"
SETTING_TAB_SIZE = "tab-size"
SETTING_TRANSLATE_TABS = "translate-tabs"
SETTING_COLOR_SCHEME_ID = "color-scheme-id"

CACHE_FILE_NAME = "python_runner_cache.json"
SETTINGS_FILE_NAME = "python_runner_settings.json"  # <<< ADDED
EXECUTION_TIMEOUT = 30
# --- End Constants ---

# --- Default Global Settings --- <<< ADDED
DEFAULT_APP_SETTINGS = {
    SETTING_DRAW_WHITESPACES: DEFAULT_DRAW_WHITESPACES,
    SETTING_TAB_SIZE: DEFAULT_TAB_SIZE,
    SETTING_TRANSLATE_TABS: DEFAULT_TRANSLATE_TABS,
    SETTING_COLOR_SCHEME_ID: DEFAULT_STYLE_SCHEME,
}
# --- End Default Global Settings ---

# --- Default Venv Settings ---
DEFAULT_VENV_SETTINGS = {
    "use_custom_venv": False,
    "venv_folder": "",
}
# --- End Default Venv Settings ---


class PythonRunnerApp(Gtk.Window):
    """
    A simple GTK application to write and run Python code snippets with tabs,
    saving/loading all tabs, per-tab venv settings, and JSON-based global settings.
    """

    def __init__(self):
        Gtk.Window.__init__(self, title=f"Python Runner {VERSION}")

        self.set_default_size(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_size_request(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", self.on_destroy)
        self._status_timeout_id = None

        # self._setup_settings() # <<< REMOVED GSettings setup
        self.app_settings = {}  # <<< ADDED: Dictionary to hold loaded settings
        self.cache_dir_path = (
            self._get_app_cache_dir()
        )  # <<< ADDED: Store cache dir path
        self.cache_file_path = os.path.join(self.cache_dir_path, CACHE_FILE_NAME)
        self.settings_file_path = os.path.join(
            self.cache_dir_path, SETTINGS_FILE_NAME
        )  # <<< ADDED

        self._load_app_settings()  # <<< ADDED: Load settings from JSON
        self._setup_css()
        # self.cache_file_path = self._get_cache_file_path() # <<< REMOVED (path determined earlier)
        self._setup_ui()
        self._setup_hotkeys()

        cache_loaded = self._load_code_from_cache()
        if not cache_loaded:
            print("Cache not loaded, creating a default tab.")
            self._add_new_tab(add_empty=True)  # Add an empty default tab

        self.apply_settings()  # Applies loaded/default app_settings
        self.update_python_env_status()

        self.show_all()

    def on_destroy(self, _):
        """Save cache and settings *before* quitting."""
        saved_cache = self._save_code_to_cache()
        if not saved_cache:
            print("ERROR: Failed to save code cache on exit!", file=sys.stderr)

        saved_settings = self._save_app_settings()  # <<< ADDED: Save settings on exit
        if not saved_settings:
            print("ERROR: Failed to save settings on exit!", file=sys.stderr)

        Gtk.main_quit()

    # <<< RENAMED and MODIFIED: Gets the base cache directory for the app >>>
    def _get_app_cache_dir(self):
        """Gets the path to the application's cache directory."""
        cache_dir = GLib.get_user_cache_dir()
        if not cache_dir:
            cache_dir = os.path.abspath(".")  # Fallback to current directory
            print(
                f"Warning: User cache directory not found. Using '{cache_dir}'.",
                file=sys.stderr,
            )
            app_cache_dir = cache_dir
        else:
            app_cache_dir = os.path.join(
                cache_dir, APP_ID
            )  # Use app-specific subfolder

        try:
            os.makedirs(app_cache_dir, exist_ok=True)  # Ensure directory exists
        except OSError as e:
            print(
                f"Error creating cache directory '{app_cache_dir}': {e}",
                file=sys.stderr,
            )
            # Fallback if creation fails (e.g., permissions)
            fallback_dir = os.path.abspath(".")
            print(
                f"Falling back to using current directory: '{fallback_dir}'",
                file=sys.stderr,
            )
            return fallback_dir
        return app_cache_dir

    # <<< REMOVED: _get_cache_file_path (path now constructed in __init__) >>>
    # def _get_cache_file_path(self): ...

    # <<< ADDED: Load global settings from JSON file >>>
    def _load_app_settings(self):
        """
        Loads global application settings from the JSON settings file.
        If the file does not exist, it creates it with default values.
        """
        loaded_settings = {}
        settings_exist = os.path.exists(self.settings_file_path)

        if settings_exist:
            print(f"Loading settings from existing file: {self.settings_file_path}")
            try:
                with open(self.settings_file_path, "r", encoding="utf-8") as f:
                    loaded_settings = json.load(f)
                if not isinstance(loaded_settings, dict):
                    print(
                        f"Warning: Settings file '{self.settings_file_path}' does not contain a valid dictionary. Using defaults and attempting to overwrite.",
                        file=sys.stderr,
                    )
                    loaded_settings = {}  # Reset to force using defaults below
                    # Force save defaults over invalid file structure
                    self.app_settings = DEFAULT_APP_SETTINGS.copy()
                    self._save_app_settings()  # Overwrite the invalid file
                    print(
                        f"Overwrote invalid settings file with defaults: {self.settings_file_path}"
                    )
                    # No need to merge below, defaults are already set
                    print(f"Final effective settings: {self.app_settings}")
                    return  # Exit early as we just saved defaults
                print(f"Successfully loaded settings from {self.settings_file_path}")

            except json.JSONDecodeError as e:
                print(
                    f"Error decoding settings file '{self.settings_file_path}': {e}. Using defaults and attempting to overwrite.",
                    file=sys.stderr,
                )
                loaded_settings = {}  # Reset to force using defaults below
                # Force save defaults over corrupted JSON
                self.app_settings = DEFAULT_APP_SETTINGS.copy()
                self._save_app_settings()  # Overwrite the corrupted file
                print(
                    f"Overwrote corrupted settings file with defaults: {self.settings_file_path}"
                )
                # No need to merge below, defaults are already set
                print(f"Final effective settings: {self.app_settings}")
                return  # Exit early as we just saved defaults
            except Exception as e:
                # Catch other potential errors like permission issues during read
                print(
                    f"Error loading settings file '{self.settings_file_path}': {e}. Using defaults for this session.",
                    file=sys.stderr,
                )
                loaded_settings = {}  # Reset to ensure defaults are used
                # Don't try to save here if reading failed due to permissions etc.

        else:
            print(
                f"Settings file not found ({self.settings_file_path}). Creating with defaults."
            )
            # <<< ADDED: Create file with defaults if it doesn't exist >>>
            self.app_settings = DEFAULT_APP_SETTINGS.copy()
            if self._save_app_settings():  # Try to save the defaults immediately
                print(
                    f"Successfully created default settings file: {self.settings_file_path}"
                )
            else:
                print(
                    f"Warning: Failed to create default settings file. Using defaults for this session.",
                    file=sys.stderr,
                )
            # No need to merge below, defaults are already set
            print(f"Final effective settings: {self.app_settings}")
            return  # Exit early as we just created defaults

        # --- Merge loaded settings with defaults (only reached if file existed and was loaded successfully) ---
        # Start with defaults
        self.app_settings = DEFAULT_APP_SETTINGS.copy()
        # Update with loaded values, ensuring correct types
        if isinstance(loaded_settings, dict):
            updated_keys = 0
            for key, default_value in DEFAULT_APP_SETTINGS.items():
                if key in loaded_settings:
                    loaded_value = loaded_settings[key]
                    if isinstance(loaded_value, type(default_value)):
                        self.app_settings[key] = loaded_value
                        updated_keys += 1
                    else:
                        print(
                            f"Warning: Setting '{key}' in file has incorrect type ({type(loaded_value).__name__}), expected {type(default_value).__name__}. Using default value.",
                            file=sys.stderr,
                        )
                # else: Key not in loaded_settings, default value is already set

            # Check for unknown keys in the loaded file (optional cleanup/warning)
            unknown_keys = set(loaded_settings.keys()) - set(
                DEFAULT_APP_SETTINGS.keys()
            )
            if unknown_keys:
                print(
                    f"Warning: Ignoring unknown keys found in settings file: {', '.join(unknown_keys)}",
                    file=sys.stderr,
                )

            print(f"Merged {updated_keys} values from loaded settings with defaults.")
        # This else shouldn't be reached due to earlier checks, but defensive coding
        else:
            print(
                "Internal Warning: loaded_settings was not a dict during merge phase. Using pure defaults.",
                file=sys.stderr,
            )
            self.app_settings = DEFAULT_APP_SETTINGS.copy()

        print(f"Final effective settings: {self.app_settings}")

    # <<< ADDED: Save global settings to JSON file >>>
    def _save_app_settings(self):
        """Saves the current global application settings to the JSON settings file."""
        try:
            # Ensure cache directory exists (might fail in rare cases after init)
            os.makedirs(self.cache_dir_path, exist_ok=True)
            with open(self.settings_file_path, "w", encoding="utf-8") as f:
                json.dump(self.app_settings, f, indent=4)
            print(f"Settings saved to {self.settings_file_path}")
            return True
        except Exception as e:
            print(
                f"Error saving settings to '{self.settings_file_path}': {e}",
                file=sys.stderr,
            )
            return False

    def _save_code_to_cache(self):
        """Saves the code and venv settings from *all* tabs to the JSON cache file."""
        tabs_data = []
        n_pages = self.notebook.get_n_pages()
        if n_pages == 0:
            print("No tabs to save.")
            # Save an empty list for consistency? Or remove the file? Let's save empty list.
            # pass

        for i in range(n_pages):
            page_widget = self.notebook.get_nth_page(i)
            if page_widget and hasattr(page_widget, "tab_widgets"):
                tab_widgets = page_widget.tab_widgets
                code_buffer = tab_widgets["code_buffer"]
                start_iter = code_buffer.get_start_iter()
                end_iter = code_buffer.get_end_iter()
                code = code_buffer.get_text(start_iter, end_iter, False)

                venv_settings = getattr(
                    page_widget, "venv_settings", DEFAULT_VENV_SETTINGS.copy()
                )

                tabs_data.append({"code": code, "venv_settings": venv_settings})
            else:
                print(
                    f"Warning: Could not get widgets for tab index {i} during save.",
                    file=sys.stderr,
                )

        try:
            # Ensure cache directory exists
            os.makedirs(
                self.cache_dir_path, exist_ok=True
            )  # <<< CHANGED: Use stored cache dir path
            with open(self.cache_file_path, "w", encoding="utf-8") as f:
                json.dump(tabs_data, f, indent=4)
            print(
                f"Code for {len(tabs_data)} tabs saved to cache {self.cache_file_path}"
            )
            return True
        except Exception as e:
            print(
                f"Error saving code cache to '{self.cache_file_path}': {e}",
                file=sys.stderr,
            )  # <<< CHANGED: Use path in message
            return False

    def _load_code_from_cache(self):
        """Loads code and venv settings from the JSON cache file, creating tabs."""
        if not os.path.exists(self.cache_file_path):
            print(
                f"Code cache file not found: {self.cache_file_path}"
            )  # <<< CHANGED: Use path in message
            return False

        try:
            with open(self.cache_file_path, "r", encoding="utf-8") as f:
                tabs_data = json.load(f)

            if not isinstance(tabs_data, list):
                print(
                    f"Error: Code cache file format is invalid (expected a list) in '{self.cache_file_path}'.",  # <<< CHANGED
                    file=sys.stderr,
                )
                return False

            if not tabs_data:
                print(
                    f"Code cache file '{self.cache_file_path}' is empty."
                )  # <<< CHANGED
                return False  # Treat empty cache same as non-existent cache for initial tab creation

            # Clear existing tabs before loading
            while self.notebook.get_n_pages() > 0:
                self.notebook.remove_page(0)

            for i, tab_data in enumerate(tabs_data):
                code = tab_data.get("code", "")
                venv_settings = tab_data.get(
                    "venv_settings", DEFAULT_VENV_SETTINGS.copy()
                )

                if (
                    not isinstance(venv_settings, dict)
                    or "use_custom_venv" not in venv_settings
                    or "venv_folder" not in venv_settings
                ):
                    print(
                        f"Warning: Invalid venv_settings format for tab {i+1} in cache. Using defaults.",
                        file=sys.stderr,
                    )
                    venv_settings = DEFAULT_VENV_SETTINGS.copy()

                self._add_tab_with_content(code, venv_settings)

            print(
                f"Loaded {len(tabs_data)} tabs from cache {self.cache_file_path}."
            )  # <<< CHANGED
            self._set_status_message("Code loaded from cache.", temporary=True)
            if self.notebook.get_n_pages() > 0:
                self.notebook.set_current_page(0)
            return True

        except json.JSONDecodeError as e:
            print(
                f"Error decoding cache file ({self.cache_file_path}): {e}",
                file=sys.stderr,
            )
            return False
        except Exception as e:
            print(
                f"Error loading from cache ({self.cache_file_path}): {e}",
                file=sys.stderr,
            )  # <<< CHANGED
            self._set_status_message("Error loading code from cache.", temporary=True)
            return False

    # <<< REMOVED: GSettings setup method >>>
    # def _setup_settings(self): ...

    def _setup_css(self):
        """Loads and applies CSS styles."""
        css_provider = Gtk.CssProvider()
        # CSS for selection remains useful
        css = f"""
        textview text selection:focus, textview text selection {{
            background-color: alpha(#333333, 0.5);
        }}
        """
        try:
            css_provider.load_from_data(css.encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        except Exception as e:
            print(f"Error loading CSS: {e}", file=sys.stderr)

    def _setup_ui(self):
        """Builds the main UI structure with tabs."""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(vbox)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        vbox.pack_start(self.notebook, True, True, 0)
        self.notebook.connect("switch-page", self.on_tab_switched)
        self.notebook.connect("page-removed", self.on_page_removed)

        status_box = self._setup_statusbar()
        vbox.pack_start(status_box, False, False, 0)

    def _add_new_tab(self, add_empty=False, inherit_settings=True):
        """Adds a new tab to the notebook.
        If add_empty is True, it adds a blank tab.
        If inherit_settings is True, it copies venv settings from the current tab.
        """
        initial_venv_settings = DEFAULT_VENV_SETTINGS.copy()
        if inherit_settings:
            current_tab_widgets = self._get_current_tab_widgets()
            if current_tab_widgets:
                current_page_index = self.notebook.get_current_page()
                if current_page_index != -1:  # Check if a tab is actually selected
                    current_paned = self.notebook.get_nth_page(current_page_index)
                    if current_paned and hasattr(current_paned, "venv_settings"):
                        initial_venv_settings = current_paned.venv_settings.copy()

        code = "" if add_empty else "# New Tab"  # Provide some placeholder if not empty
        self._add_tab_with_content(code, initial_venv_settings)

    def _add_tab_with_content(self, code, venv_settings):
        """Adds a new tab with the given code and venv_settings."""
        tab_content_paned = self._create_tab_content(venv_settings)

        code_buffer = tab_content_paned.tab_widgets["code_buffer"]
        code_buffer.set_text(code or "", -1)  # Ensure code is not None

        # Create a more dynamic tab label (e.g., "Tab N" or based on content later)
        n_pages = self.notebook.get_n_pages()
        tab_label_widget = Gtk.Label(label=f"Tab {n_pages + 1}")
        # You could potentially add a close button to the label here later

        self.notebook.append_page(tab_content_paned, tab_label_widget)
        self.notebook.show_all()  # Ensure the new tab content is visible
        new_page_index = self.notebook.get_n_pages() - 1
        self.notebook.set_current_page(new_page_index)

        # Apply the currently loaded global settings to the new tab
        self.apply_global_settings_to_tab(new_page_index)
        self.update_python_env_status()  # Update status bar for the new tab

    def _create_tab_content(self, initial_venv_settings):
        """Creates the content (Paned with code and output views) for a single tab."""
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)

        # Store venv settings directly on the Paned widget
        paned.venv_settings = initial_venv_settings.copy()
        paned.tab_widgets = {}  # Dictionary to hold widgets for easy access

        # --- Code Input View ---
        code_buffer = GtkSource.Buffer()
        code_input = GtkSource.View.new_with_buffer(code_buffer)  # Use constructor

        # Apply Syntax Highlighting (Python)
        lang_manager = GtkSource.LanguageManager.get_default()
        python_lang = lang_manager.get_language("python3") or lang_manager.get_language(
            "python"
        )
        if python_lang:
            code_buffer.set_language(python_lang)
        else:
            print("Warning: Python syntax highlighting not available.", file=sys.stderr)

        # Apply initial Color Scheme (will be overridden by apply_settings)
        style_manager = GtkSource.StyleSchemeManager.get_default()
        initial_scheme_id = self.app_settings.get(
            SETTING_COLOR_SCHEME_ID, DEFAULT_STYLE_SCHEME
        )
        scheme = style_manager.get_scheme(initial_scheme_id)
        if scheme:
            code_buffer.set_style_scheme(scheme)
        else:
            print(
                f"Warning: Initial scheme '{initial_scheme_id}' not found.",
                file=sys.stderr,
            )

        # Editor Features
        code_input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        code_input.set_monospace(True)
        code_input.set_show_line_numbers(True)
        code_input.set_highlight_current_line(True)
        code_input.set_auto_indent(True)
        code_input.set_indent_on_tab(True)
        # Tab settings applied by apply_settings, set defaults here just in case
        code_input.set_tab_width(
            self.app_settings.get(SETTING_TAB_SIZE, DEFAULT_TAB_SIZE)
        )
        code_input.set_insert_spaces_instead_of_tabs(
            self.app_settings.get(SETTING_TRANSLATE_TABS, DEFAULT_TRANSLATE_TABS)
        )

        # Margins
        margin = 10
        code_input.set_left_margin(margin)
        code_input.set_right_margin(margin)
        code_input.set_top_margin(margin)
        code_input.set_bottom_margin(margin)

        # Whitespace Drawing (applied by apply_settings)
        space_drawer = code_input.get_space_drawer()
        space_drawer.set_enable_matrix(True)  # Needed for detailed control

        # Scrolled Window for Code Input
        scrolled_code = Gtk.ScrolledWindow()
        scrolled_code.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_code.set_hexpand(True)
        scrolled_code.set_vexpand(True)
        scrolled_code.add(code_input)
        paned.add1(scrolled_code)  # Add to top pane

        # --- Output View ---
        output_buffer = Gtk.TextBuffer()
        output_view = Gtk.TextView(buffer=output_buffer)
        output_view.set_editable(False)
        output_view.set_monospace(True)
        output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        # Margins
        output_view.set_left_margin(margin)
        output_view.set_right_margin(margin)
        output_view.set_top_margin(margin)
        output_view.set_bottom_margin(margin)

        # Scrolled Window for Output
        scrolled_output = Gtk.ScrolledWindow()
        scrolled_output.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_output.set_hexpand(True)
        scrolled_output.set_vexpand(True)  # Allow output to take space
        scrolled_output.add(output_view)
        paned.add2(scrolled_output)  # Add to bottom pane

        # Initial position of the divider
        paned.set_position(INITIAL_HEIGHT // 2)

        # Store widgets in the dictionary for later access
        paned.tab_widgets = {
            "code_input": code_input,
            "code_buffer": code_buffer,
            "output_buffer": output_buffer,
            "output_view": output_view,
            "space_drawer": space_drawer,
            "paned": paned,  # Reference to the container itself if needed
        }

        return paned

    def _setup_statusbar(self):
        """Creates the status bar area."""
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_box.set_border_width(0)
        status_box.set_margin_start(6)
        status_box.set_margin_end(6)
        status_box.set_margin_top(0)
        status_box.set_margin_bottom(5)

        self.status_label = Gtk.Label(label="Ready", xalign=0.0)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        status_box.pack_start(self.status_label, True, True, 0)

        # Could add more status indicators here later (e.g., line/col number)

        return status_box

    def _setup_hotkeys(self):
        """Sets up global hotkeys for application actions."""
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)

        # Define hotkeys and connect them to methods
        key, mod = Gtk.accelerator_parse("<Control>R")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_run_clicked)

        # Note: Ctrl+C is often handled natively by text views for clipboard copy.
        # This custom handler copies the *entire* buffer if nothing is selected.
        key, mod = Gtk.accelerator_parse("<Control>C")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_copy_clicked)

        key, mod = Gtk.accelerator_parse("<Control>S")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_export_clicked)

        # Settings Hotkeys
        key, mod = Gtk.accelerator_parse("<Control>T")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_settings_clicked)
        key, mod = Gtk.accelerator_parse("<Control>comma")  # Alternative
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_settings_clicked)

        key, mod = Gtk.accelerator_parse("<Control>H")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_show_hotkeys)

        # Tab Management Hotkeys
        key, mod = Gtk.accelerator_parse("<Control>N")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_new_tab_clicked)
        key, mod = Gtk.accelerator_parse("<Control>W")
        accel_group.connect(
            key, mod, Gtk.AccelFlags.VISIBLE, self.on_remove_tab_clicked
        )

        # Pip Freeze Hotkey
        key, mod = Gtk.accelerator_parse(
            "<Control>P"
        )  # Changed from Ctrl+Shift+P for simplicity
        accel_group.connect(
            key, mod, Gtk.AccelFlags.VISIBLE, self.on_pip_freeze_clicked
        )

        # TODO: Consider adding hotkeys for switching tabs (e.g., Ctrl+PageUp/PageDown)

    def _get_current_tab_widgets(self):
        """Gets the widgets associated with the current tab's Paned."""
        current_page_index = self.notebook.get_current_page()
        if current_page_index == -1:  # No tab selected/exists
            return None
        paned = self.notebook.get_nth_page(current_page_index)
        # Check if the page widget is valid and has our expected structure
        if (
            paned
            and hasattr(paned, "tab_widgets")
            and isinstance(paned.tab_widgets, dict)
        ):
            return paned.tab_widgets
        else:
            print(
                f"Warning: Could not find expected widgets structure for current tab index {current_page_index}",
                file=sys.stderr,
            )
            return None

    def _run_code_thread(
        self, code, python_interpreter, output_buffer, output_view, source_view
    ):
        """Worker thread function to execute Python code."""
        output = ""
        error = ""
        success = False
        process = None

        try:
            process = subprocess.Popen(
                [python_interpreter, "-u", "-c", code],  # -u for unbuffered output
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,  # Decode stdout/stderr as text
                encoding="utf-8",  # Specify encoding
                errors="replace",  # Handle potential decoding errors
                # Consider adding `cwd` if running relative to a specific directory is needed
            )

            # Communicate with the process, wait for completion with timeout
            stdout_data, stderr_data = process.communicate(timeout=EXECUTION_TIMEOUT)

            if process.returncode == 0:
                output = stdout_data
                if stderr_data:  # Include stderr even on success (for warnings)
                    error = f"--- Warnings/Stderr Output ---\n{stderr_data}"
                success = True
            else:
                output = stdout_data  # Show stdout even if there was an error
                error = f"--- Error (Exit Code {process.returncode}) ---\n{stderr_data}"

        except FileNotFoundError:
            error = f"Error: Python interpreter '{python_interpreter}' not found."
            success = False
        except subprocess.TimeoutExpired:
            if process:
                process.kill()  # Ensure the process is terminated
                # Try to get any remaining output after killing
                stdout_data, stderr_data = process.communicate()
                output = stdout_data
                error = f"--- Error: Code execution timed out after {EXECUTION_TIMEOUT} seconds ---\n{stderr_data}"
            else:  # Should not happen if Popen succeeded, but handle defensively
                error = f"Error: Code execution timed out after {EXECUTION_TIMEOUT} seconds."
            success = False
        except Exception as e:
            error = f"Error executing code: {e}"
            success = False
            # Ensure process is killed if it's still running after an unexpected exception
            if process and process.poll() is None:
                try:
                    process.kill()
                    process.communicate()  # Clean up pipes
                except Exception as kill_e:
                    print(
                        f"Error trying to kill process after exception: {kill_e}",
                        file=sys.stderr,
                    )

        # Schedule the UI update on the main GTK thread
        GLib.idle_add(
            self._update_output_view,
            output,
            error,
            success,
            output_buffer,
            output_view,
            source_view,  # Pass the source_view to check if the tab is still active
        )

    def _update_output_view(
        self, output_text, error_text, success, output_buffer, output_view, source_view
    ):
        """Updates the output view and restores status (runs in main thread)."""
        full_output = output_text or ""
        if error_text:
            if full_output:
                full_output += "\n" + error_text
            else:
                full_output = error_text

        output_buffer.set_text(full_output)
        # Scroll to the end of the output
        end_iter = output_buffer.get_end_iter()
        output_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 0.0)

        # Restore the default status (Python env info) *only if* the tab that
        # initiated the run is still the active tab.
        current_widgets = self._get_current_tab_widgets()
        active_source_view = current_widgets["code_input"] if current_widgets else None
        if source_view == active_source_view:
            self._restore_default_status()

        # Required for GLib.idle_add callback
        return GLib.SOURCE_REMOVE

    def on_run_clicked(self, *args):
        """Handles the Run action, triggered by hotkey (Ctrl+R) or button, on the current tab."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            self._set_status_message("No active tab found.", temporary=True)
            return

        code_buffer = tab_widgets["code_buffer"]
        output_buffer = tab_widgets["output_buffer"]
        output_view = tab_widgets["output_view"]
        code_input = tab_widgets["code_input"]  # Used for status message association

        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if not code.strip():
            self._set_status_message(
                "Nothing to run.", temporary=True, temporary_source_view=code_input
            )
            return

        python_interpreter = self.get_python_interpreter()
        # Check if the interpreter path indicates an error/warning state
        if (
            "Warning:" in python_interpreter
            or "Not Found" in python_interpreter
            or "No active tab" in python_interpreter
        ):
            error_msg = f"Error: Invalid Python interpreter selected ({python_interpreter}). Check settings (Ctrl+T)."
            self._set_status_message(
                error_msg, temporary=False
            )  # Make error persistent
            output_buffer.set_text(error_msg)  # Also show in output
            return

        output_buffer.set_text("")  # Clear previous output

        # Run the code in a separate thread
        thread = threading.Thread(
            target=self._run_code_thread,
            args=(
                code,
                python_interpreter,
                output_buffer,
                output_view,
                code_input,  # Pass the source_view for context
            ),
            daemon=True,  # Allow app to exit even if thread is running (due to timeout kill)
        )
        thread.start()

    def on_copy_clicked(self, *args):
        """Handles the Copy action, triggered by hotkey (Ctrl+C), on the current tab.
        Copies selection if available, otherwise copies the entire code buffer.
        """
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return  # No active tab

        code_buffer = tab_widgets["code_buffer"]
        code_input = tab_widgets["code_input"]  # For status message association

        # Check if there is a text selection in the code buffer
        if code_buffer.get_has_selection():
            start, end = code_buffer.get_selection_bounds()
            text_to_copy = code_buffer.get_text(start, end, True)  # Get selected text
        else:
            # No selection, get the entire buffer content
            start_iter = code_buffer.get_start_iter()
            end_iter = code_buffer.get_end_iter()
            text_to_copy = code_buffer.get_text(start_iter, end_iter, False)

        if text_to_copy:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text_to_copy, -1)  # -1 means length is auto-calculated
            self._set_status_message(
                "Code copied to clipboard",
                temporary=True,
                temporary_source_view=code_input,
            )
        else:
            self._set_status_message(
                "Nothing to copy", temporary=True, temporary_source_view=code_input
            )

    def on_export_clicked(self, *args):
        """Handles the Export action (Save As), triggered by hotkey (Ctrl+S), on the current tab."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return  # No active tab

        code_buffer = tab_widgets["code_buffer"]
        code_input = tab_widgets["code_input"]  # For status message

        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if not code.strip():
            self._set_status_message(
                "No code to export", temporary=True, temporary_source_view=code_input
            )
            return

        # Create FileChooserDialog for saving
        dialog = Gtk.FileChooserDialog(
            title="Export Code As...",
            parent=self,  # Set parent window
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE,
            Gtk.ResponseType.OK,
        )
        dialog.set_do_overwrite_confirmation(True)  # Ask before overwriting
        dialog.set_current_name("script.py")  # Suggest a filename

        # Add file filters
        py_filter = Gtk.FileFilter()
        py_filter.set_name("Python files (*.py)")
        py_filter.add_pattern("*.py")
        dialog.add_filter(py_filter)

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files (*.*)")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)  # Add second so Python filter is default

        response = dialog.run()
        filename = None  # Initialize filename

        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            # Automatically add .py extension if Python filter is selected and missing
            selected_filter = dialog.get_filter()
            if (
                selected_filter == py_filter
                and filename
                and not filename.lower().endswith(".py")
            ):
                filename += ".py"

            if filename:  # Ensure a filename was actually obtained
                try:
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(code)
                    self._set_status_message(
                        f"Code exported to {os.path.basename(filename)}",
                        temporary=True,
                        temporary_source_view=code_input,
                    )
                except Exception as e:
                    print(f"Error saving file '{filename}': {e}", file=sys.stderr)
                    # Show an error dialog to the user
                    error_dialog = Gtk.MessageDialog(
                        transient_for=self,
                        flags=0,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.OK,
                        text="Error Exporting File",
                    )
                    error_dialog.format_secondary_text(f"Could not save file:\n{e}")
                    error_dialog.run()
                    error_dialog.destroy()
                    self._set_status_message(
                        f"Error exporting file",
                        temporary=True,
                        temporary_source_view=code_input,
                    )
            else:
                # This case should be rare if response is OK, but handle defensively
                print(
                    "Error: FileChooserDialog returned OK but no filename.",
                    file=sys.stderr,
                )
                self._set_status_message(
                    "Export failed (no filename)",
                    temporary=True,
                    temporary_source_view=code_input,
                )

        elif response == Gtk.ResponseType.CANCEL:
            self._set_status_message(
                "Export cancelled",
                temporary=True,
                temporary_source_view=code_input,
            )
        # Else: Other response types (like delete event), treat as cancel

        dialog.destroy()  # Clean up the dialog window

    def on_settings_clicked(self, *args):
        """Shows the settings dialog, including per-tab venv options and global JSON settings."""
        current_tab_index = self.notebook.get_current_page()
        current_paned = (
            self.notebook.get_nth_page(current_tab_index)
            if current_tab_index != -1
            else None
        )

        # Get venv settings for the *current* tab
        if not current_paned or not hasattr(current_paned, "venv_settings"):
            print(
                "Warning: Could not get current tab's venv settings for dialog. Using defaults.",
                file=sys.stderr,
            )
            # Use defaults but don't modify the non-existent tab's settings later
            current_tab_venv_settings = DEFAULT_VENV_SETTINGS.copy()
            can_modify_tab_settings = False
        else:
            # Get a copy to modify in the dialog without affecting the tab until Apply/OK
            current_tab_venv_settings = current_paned.venv_settings.copy()
            can_modify_tab_settings = True

        # Get global settings from our loaded dictionary
        # Use .get() with defaults for safety, though _load_app_settings should ensure keys exist
        current_draw_whitespaces = self.app_settings.get(
            SETTING_DRAW_WHITESPACES, DEFAULT_DRAW_WHITESPACES
        )
        current_tab_size = self.app_settings.get(SETTING_TAB_SIZE, DEFAULT_TAB_SIZE)
        current_translate_tabs = self.app_settings.get(
            SETTING_TRANSLATE_TABS, DEFAULT_TRANSLATE_TABS
        )
        current_color_scheme_id = self.app_settings.get(
            SETTING_COLOR_SCHEME_ID, DEFAULT_STYLE_SCHEME
        )

        dialog = Gtk.Dialog(
            title="Settings",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_APPLY,
            Gtk.ResponseType.APPLY,  # Allow applying without closing
            Gtk.STOCK_OK,
            Gtk.ResponseType.OK,
        )
        dialog.set_resizable(False)
        dialog.set_default_response(Gtk.ResponseType.OK)  # Enter key triggers OK

        content_area = dialog.get_content_area()
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=12)
        content_area.add(main_vbox)

        # --- Global Editor Settings Section ---
        global_frame = Gtk.Frame(label="Global Editor Settings")
        main_vbox.pack_start(global_frame, False, False, 0)
        global_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        global_frame.add(global_vbox)

        # Get available color schemes
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme_ids = style_manager.get_scheme_ids() or []
        schemes_data = []
        if scheme_ids:
            for scheme_id in sorted(scheme_ids):
                scheme = style_manager.get_scheme(scheme_id)
                if scheme:
                    schemes_data.append(
                        {"id": scheme_id, "name": scheme.get_name() or scheme_id}
                    )
            # Sort schemes alphabetically by display name for the combo box
            schemes_data.sort(key=lambda x: x["name"].lower())

        # Draw Whitespaces Toggle
        dw_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dw_label = Gtk.Label(label="Draw Whitespaces:", xalign=0.0)
        dw_switch = Gtk.Switch(
            active=current_draw_whitespaces
        )  # <<< CHANGED: Read from self.app_settings
        dw_hbox.pack_start(dw_label, True, True, 0)
        dw_hbox.pack_end(dw_switch, False, False, 0)
        global_vbox.pack_start(dw_hbox, False, False, 0)

        # Color Scheme Selector
        cs_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cs_label = Gtk.Label(label="Color Scheme:", xalign=0.0)
        cs_combo = Gtk.ComboBoxText()
        active_index = -1
        for i, scheme_info in enumerate(schemes_data):
            cs_combo.append(scheme_info["id"], scheme_info["name"])
            if (
                scheme_info["id"] == current_color_scheme_id
            ):  # <<< CHANGED: Read from self.app_settings
                active_index = i
        if active_index != -1:
            cs_combo.set_active(active_index)
        elif schemes_data:  # Fallback to first item if current not found
            cs_combo.set_active(0)
            print(
                f"Warning: Saved color scheme '{current_color_scheme_id}' not found. Select default.",
                file=sys.stderr,
            )

        cs_combo.set_size_request(150, -1)  # Give combo box some width
        cs_hbox.pack_start(cs_label, False, False, 0)  # Label doesn't expand
        cs_hbox.pack_start(cs_combo, True, True, 0)  # Combo box expands
        global_vbox.pack_start(cs_hbox, False, False, 0)

        # Tab Size Spinner
        ts_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ts_label = Gtk.Label(label="Tab Size (Spaces):", xalign=0.0)
        ts_spin = Gtk.SpinButton.new_with_range(1, 16, 1)  # Range 1 to 16, step 1
        ts_spin.set_value(current_tab_size)  # <<< CHANGED: Read from self.app_settings
        ts_hbox.pack_start(ts_label, True, True, 0)
        ts_hbox.pack_end(ts_spin, False, False, 0)
        global_vbox.pack_start(ts_hbox, False, False, 0)

        # Translate Tabs (Spaces vs Tabs) Toggle
        tt_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tt_label = Gtk.Label(label="Use Spaces Instead of Tabs:", xalign=0.0)
        tt_switch = Gtk.Switch(
            active=current_translate_tabs
        )  # <<< CHANGED: Read from self.app_settings
        tt_hbox.pack_start(tt_label, True, True, 0)
        tt_hbox.pack_end(tt_switch, False, False, 0)
        global_vbox.pack_start(tt_hbox, False, False, 0)

        # --- Current Tab Python Environment Section ---
        tab_frame = Gtk.Frame(label="Current Tab Python Environment")
        main_vbox.pack_start(tab_frame, False, False, 0)
        tab_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        tab_frame.add(tab_vbox)

        # Get current tab's venv settings (already copied)
        use_custom_venv_tab = current_tab_venv_settings.get("use_custom_venv", False)
        venv_folder_tab = current_tab_venv_settings.get("venv_folder", "")

        # Use Custom Venv Toggle
        cv_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cv_label = Gtk.Label(label="Use Custom Virtual Environment:", xalign=0.0)
        cv_switch = Gtk.Switch(active=use_custom_venv_tab)
        cv_hbox.pack_start(cv_label, True, True, 0)
        cv_hbox.pack_end(cv_switch, False, False, 0)
        tab_vbox.pack_start(cv_hbox, False, False, 0)

        # Venv Path Selection
        vp_outer_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        vp_label = Gtk.Label(label="Venv Path:", xalign=0.0)
        vp_controls_hbox = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )  # Entry and Button
        vp_entry = Gtk.Entry(
            text=venv_folder_tab,
            sensitive=cv_switch.get_active(),  # Initially sensitive based on switch
            xalign=0.0,  # Align text left
            placeholder_text="Path to venv directory (e.g., /path/to/myenv)",
        )
        vp_button = Gtk.Button(label="Browse...", sensitive=cv_switch.get_active())
        vp_controls_hbox.pack_start(vp_entry, True, True, 0)  # Entry expands
        vp_controls_hbox.pack_start(vp_button, False, False, 0)  # Button fixed size
        vp_outer_hbox.pack_start(vp_label, False, False, 0)  # Label fixed size
        vp_outer_hbox.pack_start(
            vp_controls_hbox, True, True, 0
        )  # Controls box expands
        tab_vbox.pack_start(vp_outer_hbox, False, False, 0)

        # Enable/disable venv path controls based on the switch
        def _toggle_venv_widgets(switch, *args):
            is_active = switch.get_active()
            vp_entry.set_sensitive(is_active)
            vp_button.set_sensitive(is_active)

        cv_switch.connect("notify::active", _toggle_venv_widgets)

        # Browse for Venv Folder Button Handler
        def _browse_venv(button):
            folder_dialog = Gtk.FileChooserDialog(
                title="Select Venv Folder",
                parent=dialog,  # Parent is the settings dialog
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            folder_dialog.add_buttons(
                Gtk.STOCK_CANCEL,
                Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OPEN,
                Gtk.ResponseType.OK,  # Use "Open" even for folders
            )
            # Try to set the initial folder based on the entry
            current_path = vp_entry.get_text()
            if current_path and os.path.isdir(current_path):
                try:
                    folder_dialog.set_current_folder(current_path)
                except GLib.Error as e:  # Handle potential errors setting folder
                    print(
                        f"Warning: Could not set folder dialog path to '{current_path}': {e}",
                        file=sys.stderr,
                    )
            elif os.path.isdir(os.path.expanduser("~")):  # Fallback to home directory
                folder_dialog.set_current_folder(os.path.expanduser("~"))

            browse_response = folder_dialog.run()
            if browse_response == Gtk.ResponseType.OK:
                venv_folder = folder_dialog.get_filename()
                if venv_folder:  # Check if a folder was selected
                    vp_entry.set_text(venv_folder)
            folder_dialog.destroy()

        vp_button.connect("clicked", _browse_venv)

        # Function to apply changes (called by Apply and OK)
        def _apply_changes():
            print("Applying settings changes...")
            settings_changed = False
            tab_settings_changed = False

            # --- Apply Global Settings ---
            new_scheme_id = cs_combo.get_active_id()
            if (
                new_scheme_id
                and self.app_settings.get(SETTING_COLOR_SCHEME_ID) != new_scheme_id
            ):
                self.app_settings[SETTING_COLOR_SCHEME_ID] = new_scheme_id
                settings_changed = True

            new_draw_ws = dw_switch.get_active()
            if self.app_settings.get(SETTING_DRAW_WHITESPACES) != new_draw_ws:
                self.app_settings[SETTING_DRAW_WHITESPACES] = new_draw_ws
                settings_changed = True

            new_tab_size = ts_spin.get_value_as_int()
            if self.app_settings.get(SETTING_TAB_SIZE) != new_tab_size:
                self.app_settings[SETTING_TAB_SIZE] = new_tab_size
                settings_changed = True

            new_translate_tabs = tt_switch.get_active()
            if self.app_settings.get(SETTING_TRANSLATE_TABS) != new_translate_tabs:
                self.app_settings[SETTING_TRANSLATE_TABS] = new_translate_tabs
                settings_changed = True

            # --- Apply Current Tab Settings ---
            if (
                can_modify_tab_settings
                and current_paned
                and hasattr(current_paned, "venv_settings")
            ):
                new_use_custom = cv_switch.get_active()
                new_venv_path = vp_entry.get_text()
                if current_paned.venv_settings.get("use_custom_venv") != new_use_custom:
                    current_paned.venv_settings["use_custom_venv"] = new_use_custom
                    tab_settings_changed = True
                if current_paned.venv_settings.get("venv_folder") != new_venv_path:
                    current_paned.venv_settings["venv_folder"] = new_venv_path
                    tab_settings_changed = True

                if tab_settings_changed:
                    print(
                        f"Applied venv settings for tab {current_tab_index}: {current_paned.venv_settings}"
                    )
            elif not can_modify_tab_settings:
                print("No active tab, skipping per-tab venv settings application.")
            else:  # Should not happen if can_modify_tab_settings is True
                print(
                    "Warning: Could not apply venv settings (current tab pane invalid).",
                    file=sys.stderr,
                )

            # If any settings changed, apply them visually and save
            if settings_changed:
                print("Global settings changed, applying to UI and saving...")
                self.apply_settings()  # Apply global settings to all tabs
                self._save_app_settings()  # Save the updated self.app_settings dict to JSON

            if tab_settings_changed:
                print("Tab venv settings changed, updating status bar...")
                self.update_python_env_status()  # Update status bar based on new venv setting

            if settings_changed or tab_settings_changed:
                self._set_status_message("Settings applied.", temporary=True)
            else:
                self._set_status_message("Settings unchanged.", temporary=True)

        # Dialog interaction loop
        dialog.show_all()  # Make the dialog and its contents visible
        while True:
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                _apply_changes()
                break  # Exit loop and destroy dialog
            elif response == Gtk.ResponseType.APPLY:
                _apply_changes()
                # Do not break, keep dialog open
            elif (
                response == Gtk.ResponseType.CANCEL
                or response
                == Gtk.ResponseType.DELETE_EVENT  # Handle closing via window manager
            ):
                # No changes applied on Cancel/Close, update status just in case venv was visually different
                self.update_python_env_status()
                self._set_status_message("Settings cancelled.", temporary=True)
                break  # Exit loop and destroy dialog

        dialog.destroy()  # Clean up the dialog

    # <<< REMOVED: GSettings change signal handler >>>
    # def on_settings_changed(self, settings, key): ...

    def on_new_tab_clicked(self, *args):
        """Handles the New Tab action, triggered by hotkey (Ctrl+N)."""
        # Inherit venv settings from current tab by default
        self._add_new_tab(add_empty=True, inherit_settings=True)

        # Get the input view of the *newly created* tab for status message association
        new_tab_widgets = self._get_current_tab_widgets()
        new_code_input = new_tab_widgets["code_input"] if new_tab_widgets else None

        self._set_status_message(
            "New Tab Added", temporary=True, temporary_source_view=new_code_input
        )

    def on_show_hotkeys(self, *args):
        """Displays a list of available hotkeys in the current tab's output window."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            self._set_status_message(
                "No active tab to show hotkeys in.", temporary=True
            )
            return

        output_buffer = tab_widgets["output_buffer"]
        output_view = tab_widgets["output_view"]

        hotkey_list = """--- Hotkeys ---
Ctrl+R      : Run Code
Ctrl+C      : Copy Code/Selection
Ctrl+S      : Export Code to File...
Ctrl+T      : Open Settings
Ctrl+H      : Show Hotkeys (this list)
Ctrl+N      : New Tab
Ctrl+W      : Remove Current Tab
Ctrl+P      : Pip Freeze (current env)
Ctrl+PgUp   : Previous Tab (Notebook default)
Ctrl+PgDown : Next Tab (Notebook default)
"""
        if output_buffer and output_view:
            output_buffer.set_text(hotkey_list)
            # Scroll output view to the top to ensure the list title is visible
            start_iter = output_buffer.get_start_iter()
            output_view.scroll_to_iter(start_iter, 0.0, False, 0.0, 0.0)
            self._set_status_message("Displayed hotkeys", temporary=True)
        else:
            # This should generally not happen if a tab exists
            print(
                "Warning: Could not display hotkeys, output view not found.",
                file=sys.stderr,
            )
            self._set_status_message("Error displaying hotkeys.", temporary=True)

    def apply_settings(self):
        """Applies current application-wide (JSON settings) values to the UI of *all* tabs."""
        print(
            "Applying application-wide settings from self.app_settings to all tabs..."
        )
        n_pages = self.notebook.get_n_pages()
        if n_pages > 0:
            for i in range(n_pages):
                self.apply_global_settings_to_tab(i)
            print(f"Application-wide settings applied to {n_pages} tabs.")
        else:
            print("No tabs open, skipping settings application.")

    def apply_global_settings_to_tab(self, page_index):
        """Applies global editor settings (theme, whitespace, tabs) from self.app_settings to a specific tab."""
        paned = self.notebook.get_nth_page(page_index)
        # Check if the page widget is valid and has our expected structure
        if (
            not paned
            or not hasattr(paned, "tab_widgets")
            or not isinstance(paned.tab_widgets, dict)
        ):
            print(
                f"Warning: Cannot apply settings, invalid widgets structure for tab {page_index}",
                file=sys.stderr,
            )
            return

        tab_widgets = paned.tab_widgets
        code_input = tab_widgets.get("code_input")
        code_buffer = tab_widgets.get("code_buffer")
        space_drawer = tab_widgets.get("space_drawer")

        # Ensure required widgets are present
        if not code_input or not code_buffer or not space_drawer:
            print(
                f"Warning: Missing one or more required widgets in tab {page_index}. Cannot apply all settings.",
                file=sys.stderr,
            )
            # Continue applying settings for widgets that *are* present

        # --- Apply Color Scheme ---
        if code_buffer:
            style_manager = GtkSource.StyleSchemeManager.get_default()
            # Use .get() with default for robustness
            scheme_id = self.app_settings.get(
                SETTING_COLOR_SCHEME_ID, DEFAULT_STYLE_SCHEME
            )
            scheme = style_manager.get_scheme(scheme_id)
            if not scheme:
                print(
                    f"Warning: Scheme '{scheme_id}' not found. Trying fallback '{DEFAULT_STYLE_SCHEME}'.",
                    file=sys.stderr,
                )
                scheme_id = DEFAULT_STYLE_SCHEME
                scheme = style_manager.get_scheme(scheme_id)
            if not scheme:  # Try absolute fallback 'classic'
                print(
                    f"Warning: Fallback scheme '{DEFAULT_STYLE_SCHEME}' not found. Trying 'classic'.",
                    file=sys.stderr,
                )
                scheme_id = "classic"  # A very common default
                scheme = style_manager.get_scheme(scheme_id)

            if scheme:
                # Only apply if the scheme is actually different
                current_scheme = code_buffer.get_style_scheme()
                if not current_scheme or current_scheme.get_id() != scheme.get_id():
                    code_buffer.set_style_scheme(scheme)
                    # print(f"Applied scheme '{scheme.get_id()}' to tab {page_index}")
            else:
                print(
                    f"Error: Could not find any valid color scheme (tried '{scheme_id}') to apply to tab {page_index}.",
                    file=sys.stderr,
                )

        # --- Apply Draw Whitespaces ---
        if code_input and space_drawer:
            draw_whitespaces = self.app_settings.get(
                SETTING_DRAW_WHITESPACES, DEFAULT_DRAW_WHITESPACES
            )
            # Define the flags based on the setting
            required_types = GtkSource.SpaceTypeFlags.NONE
            if draw_whitespaces:
                # Combine flags for common whitespace types
                required_types = (
                    GtkSource.SpaceTypeFlags.SPACE
                    | GtkSource.SpaceTypeFlags.TAB
                    # GtkSource.SpaceTypeFlags.NEWLINE
                    # GtkSource.SpaceTypeFlags.NBSP # Optional: Non-breaking space
                    # GtkSource.SpaceTypeFlags.LEADING # Optional: Leading space
                    # GtkSource.SpaceTypeFlags.TEXT # Optional: Space within text
                    # GtkSource.SpaceTypeFlags.TRAILING # Optional: Trailing space
                )

            # Check current flags and update only if needed
            # GtkSourceView 3 uses set_matrix which isn't quite the same as checking flags.
            # We set enable_matrix(True) in _create_tab_content.
            # Then, control visibility via set_types_for_locations.
            current_types = space_drawer.get_types_for_locations(
                GtkSource.SpaceLocationFlags.ALL
            )
            if current_types != required_types:
                space_drawer.set_types_for_locations(
                    GtkSource.SpaceLocationFlags.ALL, required_types
                )
                # print(f"Applied whitespace drawing ({draw_whitespaces}) to tab {page_index}")

        # --- Apply Tab Size and Translation ---
        if code_input:
            tab_size = self.app_settings.get(SETTING_TAB_SIZE, DEFAULT_TAB_SIZE)
            translate_tabs = self.app_settings.get(
                SETTING_TRANSLATE_TABS, DEFAULT_TRANSLATE_TABS
            )

            # Apply only if changed
            if code_input.get_tab_width() != tab_size:
                code_input.set_tab_width(tab_size)
                # print(f"Applied tab size ({tab_size}) to tab {page_index}")
            if code_input.get_insert_spaces_instead_of_tabs() != translate_tabs:
                code_input.set_insert_spaces_instead_of_tabs(translate_tabs)
                # print(f"Applied translate tabs ({translate_tabs}) to tab {page_index}")

        # Request redraw if any visual settings might have changed
        if code_input:
            code_input.queue_draw()

    def get_python_interpreter(self):
        """Determines the Python interpreter path based on the *current* tab's venv settings."""
        current_tab_index = self.notebook.get_current_page()
        if current_tab_index == -1:
            # print("Warning: No active tab selected, cannot determine Python interpreter.", file=sys.stderr)
            # Return a specific string indicating this state
            return "Warning: No active tab"

        current_paned = self.notebook.get_nth_page(current_tab_index)
        # Get venv settings safely
        if not current_paned or not hasattr(current_paned, "venv_settings"):
            print(
                f"Warning: Could not get venv settings for current tab index {current_tab_index}. Using defaults.",
                file=sys.stderr,
            )
            tab_venv_settings = DEFAULT_VENV_SETTINGS  # Use default dict directly
        else:
            tab_venv_settings = current_paned.venv_settings

        use_custom_venv = tab_venv_settings.get("use_custom_venv", False)
        venv_folder = tab_venv_settings.get("venv_folder", "")

        if use_custom_venv:
            if venv_folder and os.path.isdir(venv_folder):
                # Construct path to python executable within the venv's bin directory
                # Prefer python3 if it exists
                python3_executable = os.path.join(venv_folder, "bin", "python3")
                if os.path.isfile(python3_executable) and os.access(
                    python3_executable, os.X_OK
                ):
                    # print(f"Using venv python3: {python3_executable}")
                    return python3_executable

                # Fallback to just 'python' in the venv bin
                python_executable = os.path.join(venv_folder, "bin", "python")
                if os.path.isfile(python_executable) and os.access(
                    python_executable, os.X_OK
                ):
                    # print(f"Using venv python: {python_executable}")
                    return python_executable

                # If neither found in bin, issue a warning and fall back
                print(
                    f"Warning: No executable 'python' or 'python3' found in specified venv bin: '{os.path.join(venv_folder, 'bin')}'. Falling back to system Python.",
                    file=sys.stderr,
                )
                # Fall through to system Python search below
            else:
                # If path is set but invalid, issue a warning and fall back
                if venv_folder:  # Only warn if a path was actually provided
                    print(
                        f"Warning: Custom venv path '{venv_folder}' is invalid or not a directory. Falling back to system Python.",
                        file=sys.stderr,
                    )
                # Else: use_custom_venv is True but path is empty - treat as fallback

        # Fallback: Find system Python using shutil.which (more reliable than searching PATH manually)
        # Prefer python3
        system_python3 = shutil.which("python3")
        if system_python3:
            # print(f"Using system python3: {system_python3}")
            return system_python3

        # Fallback to python if python3 not found
        system_python = shutil.which("python")
        if system_python:
            # print(f"Using system python: {system_python}")
            return system_python

        # If neither python3 nor python found in PATH
        print(
            "Error: Neither 'python3' nor 'python' found in system PATH.",
            file=sys.stderr,
        )
        return "Warning: No Python found"

    def update_python_env_status(self, source_view=None):
        """Updates the status bar with the current Python environment info for the current tab."""
        python_interpreter = self.get_python_interpreter()
        python_version = "Unknown"
        status_text = python_interpreter  # Default text if version check fails

        # Check for warning/error states from get_python_interpreter
        if python_interpreter.startswith("Warning:"):
            status_text = f"Python Env: {python_interpreter}"
        else:
            # Try to get the version using --version
            try:
                # Use a short timeout for version check
                result = subprocess.run(
                    [python_interpreter, "--version"],
                    capture_output=True,  # Capture stdout and stderr
                    text=True,  # Decode as text
                    check=False,  # Don't raise exception on non-zero exit code
                    timeout=2,  # Short timeout (2 seconds)
                    encoding="utf-8",
                    errors="replace",
                )
                # Python --version often prints to stderr, sometimes stdout
                version_output = (result.stderr or result.stdout or "").strip()

                if result.returncode == 0 and "Python" in version_output:
                    # Basic parsing, assumes format "Python X.Y.Z"
                    parts = version_output.split()
                    if len(parts) > 1:
                        python_version = parts[1]
                    else:  # Handle unexpected output format
                        python_version = version_output  # Show the whole string
                else:
                    print(
                        f"Warning: Failed to get version from '{python_interpreter}'. RC={result.returncode}, Stderr: '{result.stderr}', Stdout: '{result.stdout}'",
                        file=sys.stderr,
                    )
                    python_version = "Version N/A"

            except FileNotFoundError:
                # This case should be handled by get_python_interpreter already, but double-check
                print(
                    f"Error: Interpreter '{python_interpreter}' not found during version check.",
                    file=sys.stderr,
                )
                python_version = "Not Found"
                python_interpreter = os.path.basename(
                    python_interpreter
                )  # Show only name if path is bad
            except subprocess.TimeoutExpired:
                print(
                    f"Warning: Timeout getting version from '{python_interpreter}'",
                    file=sys.stderr,
                )
                python_version = "Timeout"
            except Exception as e:
                print(
                    f"Error checking Python version for '{python_interpreter}': {e}",
                    file=sys.stderr,
                )
                python_version = "Error"

            # Format the display path nicely
            display_path = python_interpreter
            home_dir = os.path.expanduser("~")
            if display_path.startswith(home_dir):
                display_path = (
                    "~" + display_path[len(home_dir) :]
                )  # Shorten home dir paths

            status_text = f"{display_path} ({python_version})"

        # Update the status bar label *unless* a temporary message is currently being displayed
        # Check if _status_timeout_id is active
        if not self._status_timeout_id:
            self.status_label.set_text(status_text)
        else:
            # A temporary message is active, don't overwrite it.
            # It will be restored by _restore_default_status when the timeout expires.
            pass

    def on_tab_switched(self, notebook, page, page_num):
        """Handler for tab switch event. Updates status bar."""
        print(f"Switched to tab index: {page_num}")
        # Cancel any pending temporary status message reset when switching tabs
        if self._status_timeout_id:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None
            print("Cancelled pending status restore due to tab switch.")
        # Update the status bar to reflect the new tab's environment
        self.update_python_env_status()

    def on_page_removed(self, notebook, child, page_num):
        """Handler for page removed event. Updates status bar if the active tab changed or no tabs left."""
        print(f"Tab removed at index: {page_num}")
        current_page = notebook.get_current_page()
        if current_page != -1:
            # If other tabs remain, update status for the new current tab
            self.update_python_env_status()
        else:
            # No tabs left
            if self._status_timeout_id:  # Cancel any pending restore
                GLib.source_remove(self._status_timeout_id)
                self._status_timeout_id = None
            self.status_label.set_text("No tabs open. Press Ctrl+N for a new tab.")

    def _set_status_message(
        self,
        text,
        temporary=False,
        temporary_source_view=None,  # Keep track of which tab triggered the message (optional)
        timeout=STATUS_MESSAGE_TIMEOUT_MS,
    ):
        """Updates the status bar label, optionally resetting after a delay."""
        # Always clear any existing timeout first
        if self._status_timeout_id:
            # Check if it's safe to remove (it should be unless called from the timeout handler itself)
            # GLib.source_remove returns True if removed, False if not found/already triggered
            if GLib.source_remove(self._status_timeout_id):
                # print("Cleared previous status timeout.")
                pass
            self._status_timeout_id = None  # Ensure it's cleared

        # Set the new message
        self.status_label.set_text(text)

        # If temporary, schedule the restore function
        if temporary:
            # Pass the source_view context if provided
            self._status_timeout_id = GLib.timeout_add(
                timeout,
                self._restore_default_status,
                # temporary_source_view # Optional: pass context to the callback
            )
            # print(f"Scheduled status restore in {timeout} ms (ID: {self._status_timeout_id}).")

    def _restore_default_status(self, *user_data):
        """Restores the status bar to the default (Python env info). Called by timeout or completion."""
        # print(f"Attempting to restore default status (Timeout ID {self._status_timeout_id})...")

        # Crucial: Mark the timeout as inactive *before* calling update_python_env_status,
        # because update_python_env_status checks this ID to decide if it should update the label.
        current_timeout_id = self._status_timeout_id
        self._status_timeout_id = None

        # Check if the timeout ID we were called with is the one we just cleared.
        # This helps prevent race conditions if multiple timeouts were somehow scheduled.
        # Although the logic in _set_status_message should prevent this.
        if current_timeout_id is None:
            # print("Restore called, but no active timeout ID was found (already restored or cancelled).")
            return GLib.SOURCE_REMOVE  # Must return this

        # Optional: Could use user_data (if temporary_source_view was passed) to only restore
        # if the triggering tab is still active. However, the current approach restores
        # the status based on the *currently active* tab, which might be more intuitive.
        # e.g., if user triggers copy, switches tab, then timeout expires.

        # print("Restoring default status bar text.")
        self.update_python_env_status()  # Update based on the currently active tab

        # Return SOURCE_REMOVE to unschedule the timeout callback automatically.
        return GLib.SOURCE_REMOVE

    def on_remove_tab_clicked(self, *args):
        """Handles the Remove Tab action, triggered by hotkey (Ctrl+W)."""
        current_page_index = self.notebook.get_current_page()
        if current_page_index != -1:
            print(f"Removing tab at index: {current_page_index}")
            page_widget = self.notebook.get_nth_page(current_page_index)
            self.notebook.remove_page(current_page_index)
            # Optionally: could destroy the page_widget explicitly if needed,
            # but GTK usually handles this when removing from container.

            # Get input view of the *new* current tab (if any) for status context
            new_tab_widgets = self._get_current_tab_widgets()
            new_code_input = new_tab_widgets["code_input"] if new_tab_widgets else None

            self._set_status_message(
                "Tab removed.", temporary=True, temporary_source_view=new_code_input
            )

            # on_page_removed signal handler will update status if no tabs left

        else:
            # No tab selected to remove (shouldn't happen if notebook isn't empty)
            self._set_status_message("No tab selected to remove.", temporary=True)

    def on_pip_freeze_clicked(self, *args):
        """Handles the Pip Freeze action (Ctrl+P) for the current tab's environment."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            self._set_status_message("No active tab found.", temporary=True)
            return

        output_buffer = tab_widgets["output_buffer"]
        output_view = tab_widgets["output_view"]
        code_input = tab_widgets["code_input"]  # For status context

        python_interpreter = self.get_python_interpreter()
        # Check for invalid interpreter states
        if python_interpreter.startswith("Warning:"):
            error_msg = f"Error: Cannot run pip freeze, invalid Python interpreter ({python_interpreter}). Check settings (Ctrl+T)."
            self._set_status_message(error_msg, temporary=False)  # Persistent error
            output_buffer.set_text(
                f"Error: Invalid Python interpreter selected:\n{python_interpreter}\n\nPlease check the settings for this tab (Ctrl+T)."
            )
            return

        # Set status and clear output
        self._set_status_message(
            f"Running pip freeze with {os.path.basename(python_interpreter)}...",
            temporary=False,  # Keep status until completion
            temporary_source_view=code_input,
        )
        output_buffer.set_text("Running pip freeze...\n")  # Initial message in output

        # Run pip freeze in a separate thread
        thread = threading.Thread(
            target=self._run_pip_freeze_thread,
            args=(python_interpreter, output_buffer, output_view, code_input),
            daemon=True,
        )
        thread.start()

    def _run_pip_freeze_thread(
        self, python_interpreter, output_buffer, output_view, source_view
    ):
        """Worker thread function to execute pip freeze."""
        output = ""
        error = ""
        success = False
        process = None

        try:
            # Construct the command: python -m pip freeze
            command = [python_interpreter, "-m", "pip", "freeze"]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            # Communicate with timeout
            stdout_data, stderr_data = process.communicate(timeout=EXECUTION_TIMEOUT)

            if process.returncode == 0:
                output = stdout_data
                if not output.strip():  # Handle case where no packages are installed
                    output = "# No packages installed in this environment."
                success = True
                # Include stderr even on success, as pip might output warnings
                if stderr_data:
                    error = f"--- Pip Warnings/Stderr ---\n{stderr_data}"
            else:
                # Specific check for "No module named pip"
                if "No module named pip" in stderr_data:
                    error = f"Error: 'pip' module not found for interpreter '{python_interpreter}'.\nPlease ensure pip is installed in the selected environment."
                else:  # Generic error message
                    error = f"Error running pip freeze (Exit Code: {process.returncode}):\n{stderr_data}"
                output = stdout_data  # Include any stdout even on error

        except FileNotFoundError:
            # Should be caught by get_python_interpreter, but handle defensively
            error = f"Error: Python interpreter '{python_interpreter}' not found."
        except subprocess.TimeoutExpired:
            if process:
                process.kill()
                stdout_data, stderr_data = process.communicate()  # Get remaining output
                output = stdout_data
                error = f"--- Error: pip freeze timed out after {EXECUTION_TIMEOUT} seconds ---\n{stderr_data}"
            else:
                error = (
                    f"Error: pip freeze timed out after {EXECUTION_TIMEOUT} seconds."
                )
        except Exception as e:
            error = f"Error executing pip freeze: {e}"
            if process and process.poll() is None:  # Check if process still running
                try:
                    process.kill()
                    process.communicate()
                except Exception as kill_e:
                    print(
                        f"Error trying to kill pip process after exception: {kill_e}",
                        file=sys.stderr,
                    )

        # Schedule UI update on the main thread
        GLib.idle_add(
            self._update_output_view,  # Reuse the same update function
            output,
            error,
            success,
            output_buffer,
            output_view,
            source_view,  # Pass context
        )


def main():
    # Setup application with unique ID for potential single-instance behavior
    # FLAGS_NONE allows multiple instances if needed, HANDLES_COMMAND_LINE could be added later.
    app = Gtk.Application.new(APP_ID, Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(application):
        # Check if a window for this application instance already exists
        windows = application.get_windows()
        if windows:
            print(
                "Application already running in this instance. Presenting existing window."
            )
            # Bring the existing window to the front
            windows[0].present()
        else:
            print("Application starting. Creating main window.")
            # Create and register the main application window
            window = PythonRunnerApp()
            application.add_window(window)
            # window.show_all() # show_all is called within PythonRunnerApp.__init__

    app.connect("activate", do_activate)

    # Run the GTK main loop
    exit_status = app.run(sys.argv)
    print(f"Application exiting with status: {exit_status}")
    sys.exit(exit_status)


if __name__ == "__main__":
    # Set program name early for potential use by libraries or desktop environment
    GLib.set_prgname(APP_ID)
    main()
