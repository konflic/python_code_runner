#!/usr/bin/env python3

import os
import subprocess
import threading
import sys

import gi

from python_runner.version import VERSION  # Assuming you have this, or remove it

gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "3.0")

from gi.repository import Gtk, Gio, Pango, GtkSource, Gdk, GLib

# --- Constants ---
APP_ID = "com.example.python-runner"  # Example App ID
SETTINGS_SCHEMA = APP_ID  # Schema ID should match the App ID or be specific
INITIAL_WIDTH, INITIAL_HEIGHT = 700, 500
DEFAULT_STYLE_SCHEME = "oblivion"  # Or another available theme like "classic", "tango"
STATUS_MESSAGE_TIMEOUT_MS = 2000  # 2 seconds
DEFAULT_TAB_SIZE = 4  # Default tab size in spaces
DEFAULT_TRANSLATE_TABS = True  # Default translate tabs to spaces

# Settings Keys (Application-wide settings for now, except venv)
SETTING_DRAW_WHITESPACES = "draw-whitespaces"
SETTING_TAB_SIZE = "tab-size"
SETTING_TRANSLATE_TABS = "translate-tabs"
SETTING_COLOR_SCHEME_ID = "color-scheme-id"
CACHE_FILE_NAME = "python_runner_code_cache.txt"
EXECUTION_TIMEOUT = 30
# --- End Constants ---


class PythonRunnerApp(Gtk.Window):
    """
    A simple GTK application to write and run Python code snippets with tabs, and per-tab venv settings.
    """

    def __init__(self):
        Gtk.Window.__init__(self, title=f"Python Runner {VERSION}")
        # Per-tab settings storage (for venv settings) - INITIALIZE IT *FIRST and FOREMOST*!
        self.tab_venv_settings = {}  # Dictionary to store venv settings per tab index

        self.set_default_size(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_size_request(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", self.on_destroy)
        self.connect("destroy", Gtk.main_quit)
        self._status_timeout_id = None

        self._setup_settings()
        self._setup_css()
        self._setup_ui()  # <--- Now _setup_ui and its sub-calls will find tab_venv_settings initialized
        self._setup_hotkeys()

        self.cache_file_path = self._get_cache_file_path()
        self._load_code_from_cache()

        self.apply_settings()
        self.update_python_env_status()
        self.on_show_hotkeys()

        self.show_all()

    def on_destroy(self, _):
        self._save_code_to_cache()

    def _get_cache_file_path(self):
        """Gets the path to the cache file in the user's cache directory."""
        cache_dir = GLib.get_user_cache_dir()
        if not cache_dir:
            cache_dir = "."
        return os.path.join(cache_dir, CACHE_FILE_NAME)

    def _save_code_to_cache(self):
        """Saves the current code from the current tab to the cache file."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return

        code_buffer = tab_widgets["code_buffer"]
        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        try:
            cache_dir = os.path.dirname(self.cache_file_path)
            os.makedirs(cache_dir, exist_ok=True)
            with open(self.cache_file_path, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"Code saved to cache {self.cache_file_path}")
            return True
        except Exception as e:
            print(f"Error saving to cache: {e}", file=sys.stderr)
            return True

    def _load_code_from_cache(self):
        """Loads code from the cache file into the current tab, if it exists."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return

        code_buffer = tab_widgets["code_buffer"]
        try:
            if os.path.exists(self.cache_file_path):
                with open(self.cache_file_path, "r", encoding="utf-8") as f:
                    code = f.read()
                code_buffer.set_text(code, -1)
                self._set_status_message("Code loaded from cache.", temporary=True)
        except Exception as e:
            print(f"Error loading from cache: {e}", file=sys.stderr)
            self._set_status_message("Error loading code from cache.", temporary=True)

    def _setup_settings(self):
        """Initializes GSettings."""
        schema_source = Gio.SettingsSchemaSource.get_default()
        if schema_source:
            schema = schema_source.lookup(SETTINGS_SCHEMA, True)
            if schema:
                print(f"GSettings schema '{SETTINGS_SCHEMA}' found.")
                self.settings = Gio.Settings.new(SETTINGS_SCHEMA)
                self.settings.connect("changed", self.on_settings_changed)
            else:
                print(
                    f"Warning: GSettings schema '{SETTINGS_SCHEMA}' not found. Using defaults.",
                    file=sys.stderr,
                )
                self.settings = Gio.Settings.new_with_path(
                    SETTINGS_SCHEMA, "/dev/null/"
                )
        else:
            print(
                "Warning: Default GSettings schema source not found. Using defaults.",
                file=sys.stderr,
            )
            self.settings = Gio.Settings.new_with_path(SETTINGS_SCHEMA, "/dev/null/")

    def _setup_css(self):
        """Loads and applies CSS styles."""
        css_provider = Gtk.CssProvider()
        css = f"""
        textview text selection:focus, textview text selection {{
            background-color: alpha(#333333, 0.5);
        }}
        """
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _setup_ui(self):
        """Builds the main UI structure with tabs."""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(vbox)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        vbox.pack_start(self.notebook, True, True, 0)
        self.notebook.connect(
            "switch-page", self.on_tab_switched
        )  # Connect tab switch signal

        status_box = self._setup_statusbar()
        vbox.pack_start(status_box, False, False, 0)

        self._add_new_tab()  # Add the initial tab

    def _add_new_tab(self):
        """Adds a new tab to the notebook."""
        current_tab_widgets = self._get_current_tab_widgets()
        initial_venv_settings = {}
        if current_tab_widgets:
            current_tab_index = self.notebook.get_current_page()
            initial_venv_settings = self.tab_venv_settings.get(
                current_tab_index, {}
            ).copy()  # Inherit from current tab

        tab_content = self._create_tab_content(
            initial_venv_settings
        )  # Pass initial settings
        tab_label = Gtk.Label(label=f"Tab {self.notebook.get_n_pages() + 1}")
        self.notebook.append_page(tab_content, tab_label)
        self.notebook.show_all()
        self.notebook.set_current_page(self.notebook.get_n_pages() - 1)
        self.tab_venv_settings[self.notebook.get_current_page()] = (
            initial_venv_settings.copy()
        )  # Initialize venv settings for new tab

    def _create_tab_content(self, initial_venv_settings):
        """Creates the content (Paned with code and output views) for a single tab."""
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)

        # --- Code Input Area ---
        code_input = GtkSource.View()
        code_buffer = GtkSource.Buffer()
        code_input.set_buffer(code_buffer)

        # Apply default color scheme to new tab
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = style_manager.get_scheme(DEFAULT_STYLE_SCHEME)
        if scheme:
            code_buffer.set_style_scheme(scheme)
        else:
            print(
                f"Warning: Default style scheme '{DEFAULT_STYLE_SCHEME}' not found.",
                file=sys.stderr,
            )

        lang_manager = GtkSource.LanguageManager.get_default()
        python_lang = lang_manager.get_language("python3")
        if not python_lang:
            python_lang = lang_manager.get_language("python")
        if python_lang:
            code_buffer.set_language(python_lang)
        else:
            print("Warning: Python syntax highlighting not available.", file=sys.stderr)

        code_input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        code_input.set_monospace(True)
        code_input.set_show_line_numbers(True)
        code_input.set_highlight_current_line(True)
        code_input.set_auto_indent(True)
        code_input.set_indent_on_tab(True)
        code_input.set_tab_width(DEFAULT_TAB_SIZE)  # Default tab size
        code_input.set_insert_spaces_instead_of_tabs(
            DEFAULT_TRANSLATE_TABS
        )  # Default translate tabs

        margin = 10
        code_input.set_left_margin(margin)
        code_input.set_right_margin(margin)
        code_input.set_top_margin(margin)
        code_input.set_bottom_margin(margin)

        space_drawer = code_input.get_space_drawer()
        space_drawer.set_enable_matrix(True)

        scrolled_code = Gtk.ScrolledWindow()
        scrolled_code.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_code.set_hexpand(True)
        scrolled_code.set_vexpand(True)
        scrolled_code.add(code_input)
        paned.add1(scrolled_code)

        # --- Output Area ---
        output_buffer = Gtk.TextBuffer()
        output_view = Gtk.TextView(buffer=output_buffer)
        output_view.set_editable(False)
        output_view.set_monospace(True)
        output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        output_view.set_left_margin(margin)
        output_view.set_right_margin(margin)
        output_view.set_top_margin(margin)
        output_view.set_bottom_margin(margin)

        scrolled_output = Gtk.ScrolledWindow()
        scrolled_output.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_output.set_hexpand(True)
        scrolled_output.set_vexpand(True)
        scrolled_output.add(output_view)
        paned.add2(scrolled_output)

        paned.set_position(INITIAL_HEIGHT // 2)

        tab_widgets = {
            "code_input": code_input,
            "code_buffer": code_buffer,
            "output_buffer": output_buffer,
            "output_view": output_view,  # Store output_view here
            "space_drawer": space_drawer,
            "paned": paned,
        }
        paned.tab_widgets = tab_widgets

        # Apply initial venv settings to the tab (if any inherited)
        current_tab_index = (
            self.notebook.get_n_pages()
        )  # Index of the tab being created.
        if initial_venv_settings:
            self.tab_venv_settings[current_tab_index] = (
                initial_venv_settings.copy()
            )  # Initialize venv settings
        else:
            self.tab_venv_settings[current_tab_index] = (
                {}
            )  # Initialize with empty settings if no inheritance

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

        return status_box

    def _setup_hotkeys(self):
        """Sets up global hotkeys for application actions."""
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)

        # Ctrl+R for Run
        accel_group.connect(
            Gdk.KEY_R,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_run_clicked,
        )
        # Ctrl+C for Copy
        accel_group.connect(
            Gdk.KEY_C,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_copy_clicked,
        )
        # Ctrl+S for Save/Export
        accel_group.connect(
            Gdk.KEY_S,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_export_clicked,
        )
        # Ctrl+T for Settings
        accel_group.connect(
            Gdk.KEY_T,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_settings_clicked,
        )
        # Ctrl+, for Settings (Alternative)
        accel_group.connect(
            Gdk.KEY_comma,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_settings_clicked,
        )
        # Ctrl+H for Help (Show Hotkeys)
        accel_group.connect(
            Gdk.KEY_H,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_show_hotkeys,
        )
        # Ctrl+N for New Tab
        accel_group.connect(
            Gdk.KEY_N,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_new_tab_clicked,
        )
        # Ctrl+W for Remove Tab
        accel_group.connect(
            Gdk.KEY_W,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_remove_tab_clicked,
        )
        # Ctrl+Shift+P for Pip Freeze
        accel_group.connect(
            Gdk.KEY_P,
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK,
            Gtk.AccelFlags.VISIBLE,
            self.on_pip_freeze_clicked,
        )

    def _get_current_tab_widgets(self):
        """Gets the widgets associated with the current tab."""
        current_page_index = self.notebook.get_current_page()
        if current_page_index == -1:
            return None
        paned = self.notebook.get_nth_page(current_page_index)
        return paned.tab_widgets

    def _run_code_thread(
        self, code, python_interpreter, output_buffer, output_view, source_view
    ):  # Added output_view
        """Worker thread function to execute Python code."""
        output = ""
        error = ""
        success = False
        try:
            result = subprocess.run(
                [python_interpreter, "-c", code],
                capture_output=True,
                text=True,
                check=False,
                timeout=EXECUTION_TIMEOUT,
            )
            if result.returncode == 0:
                output = result.stdout
                success = True
            else:
                output = result.stdout
                error = (
                    f"--- Error (Exit Code {result.returncode}) ---\n{result.stderr}"
                )

        except FileNotFoundError:
            error = f"Error: Python interpreter '{python_interpreter}' not found"
        except subprocess.TimeoutExpired:
            error = "Error: Code execution timed out"
        except Exception as e:
            error = f"Error executing code: {e}"

        GLib.idle_add(
            self._update_output_view,
            output,
            error,
            success,
            output_buffer,
            output_view,
            source_view,
        )  # Added output_view

    def _update_output_view(
        self, output_text, error_text, success, output_buffer, output_view, source_view
    ):  # Added output_view
        """Updates the output view and status (runs in main thread)."""
        full_output = output_text
        if error_text:
            if full_output:
                full_output += "\n" + error_text
            else:
                full_output = error_text

        output_buffer.set_text(full_output)
        end_iter = output_buffer.get_end_iter()
        output_view.scroll_to_iter(
            end_iter, 0.0, False, 0.0, 0.0
        )  # Use passed output_view

        status_msg = "Execution finished" if success else "Execution failed"
        self._set_status_message(
            status_msg, temporary=True, temporary_source_view=source_view
        )

        return GLib.SOURCE_REMOVE

    def on_run_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the Run action, triggered by hotkey (Ctrl+R), on the current tab."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return

        code_buffer = tab_widgets["code_buffer"]
        output_buffer = tab_widgets["output_buffer"]
        output_view = tab_widgets["output_view"]  # Get output_view
        code_input = tab_widgets["code_input"]

        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if not code.strip():
            self._set_status_message(
                "Nothing to run.", temporary=True, temporary_source_view=code_input
            )
            return

        python_interpreter = self.get_python_interpreter()

        self._set_status_message(
            f"Running with {os.path.basename(python_interpreter)}...",
            temporary_source_view=code_input,
        )
        output_buffer.set_text("")

        thread = threading.Thread(
            target=self._run_code_thread,
            args=(
                code,
                python_interpreter,
                output_buffer,
                output_view,
                code_input,
            ),  # Pass output_view
        )
        thread.daemon = True
        thread.start()

    def on_copy_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the Copy action, triggered by hotkey (Ctrl+C), on the current tab."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return

        code_buffer = tab_widgets["code_buffer"]
        code_input = tab_widgets["code_input"]

        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if code:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(code, -1)
            self._set_status_message(
                "Code copied to clipboard",
                temporary=True,
                temporary_source_view=code_input,
            )
        else:
            self._set_status_message(
                "Nothing to copy", temporary=True, temporary_source_view=code_input
            )

    def on_export_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the Export action, triggered by hotkey (Ctrl+S), on the current tab."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return

        code_buffer = tab_widgets["code_buffer"]
        code_input = tab_widgets["code_input"]

        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if not code.strip():
            self._set_status_message(
                "No code to save", temporary=True, temporary_source_view=code_input
            )
            return

        dialog = Gtk.FileChooserDialog(
            title="Export Code As...",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )

        dialog.add_buttons(
            "_Cancel",
            Gtk.ResponseType.CANCEL,
            "_Save",
            Gtk.ResponseType.OK,
        )

        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name("script.py")

        py_filter = Gtk.FileFilter()
        py_filter.set_name("Python files (*.py)")
        py_filter.add_pattern("*.py")
        dialog.add_filter(py_filter)

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files (*.*)")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)

        response = dialog.run()
        filename = None

        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            if (
                not filename.lower().endswith(".py")
                and dialog.get_filter() == py_filter
            ):
                filename += ".py"

            try:
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(code)
                self._set_status_message(
                    f"Code exported to {os.path.basename(filename)}",
                    temporary=True,
                    temporary_source_view=code_input,
                )
            except Exception as e:
                error_dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Error Saving File",
                )
                error_dialog.format_secondary_text(
                    f"Could not save file '{filename}':\n{e}"
                )
                error_dialog.run()
                error_dialog.destroy()
                self._set_status_message(
                    f"Error saving file",
                    temporary=True,
                    temporary_source_view=code_input,
                )

        dialog.destroy()

    def on_settings_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Shows the settings dialog, triggered by hotkey (Ctrl+T or Ctrl+,)."""
        dialog = Gtk.Dialog(title="Settings", parent=self, flags=0)
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK
        )
        dialog.set_resizable(False)

        content_area = dialog.get_content_area()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        content_area.add(vbox)

        # --- Get Style Schemes ---
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme_ids = style_manager.get_scheme_ids() or []
        schemes_data = []
        if scheme_ids:
            for scheme_id in scheme_ids:
                scheme = style_manager.get_scheme(scheme_id)
                if scheme:
                    schemes_data.append({"id": scheme_id, "name": scheme.get_name()})
            schemes_data.sort(key=lambda x: x["name"].lower())

        # --- Setting Widgets ---

        # Draw whitespaces
        dw_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dw_label = Gtk.Label(label="Draw Whitespaces:", xalign=0.0)
        dw_switch = Gtk.Switch(
            active=self.settings.get_boolean(SETTING_DRAW_WHITESPACES)
        )
        dw_hbox.pack_start(dw_label, True, True, 0)
        dw_hbox.pack_end(dw_switch, False, False, 0)
        vbox.pack_start(dw_hbox, False, False, 0)

        # Color Scheme Dropdown
        cs_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cs_label = Gtk.Label(label="Color Scheme:", xalign=0.0)
        cs_combo = Gtk.ComboBoxText()

        selected_scheme_id = self.settings.get_string(SETTING_COLOR_SCHEME_ID)
        active_index = -1
        for i, scheme_info in enumerate(schemes_data):
            cs_combo.append(scheme_info["id"], scheme_info["name"])
            if scheme_info["id"] == selected_scheme_id:
                active_index = i

        if active_index != -1:
            cs_combo.set_active(active_index)
        elif schemes_data:
            cs_combo.set_active(0)
            selected_scheme_id = schemes_data[0]["id"]

        cs_hbox.pack_start(cs_label, False, False, 0)
        cs_hbox.pack_start(cs_combo, True, True, 0)
        vbox.pack_start(cs_hbox, False, False, 0)

        # Tab size
        ts_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ts_label = Gtk.Label(label="Tab Size (Spaces):", xalign=0.0)
        ts_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
        ts_spin.set_value(self.settings.get_int(SETTING_TAB_SIZE))
        ts_hbox.pack_start(ts_label, True, True, 0)
        ts_hbox.pack_end(ts_spin, False, False, 0)
        vbox.pack_start(ts_hbox, False, False, 0)

        # Translate tabs to spaces
        tt_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tt_label = Gtk.Label(label="Use Spaces Instead of Tabs:", xalign=0.0)
        tt_switch = Gtk.Switch(active=self.settings.get_boolean(SETTING_TRANSLATE_TABS))
        tt_hbox.pack_start(tt_label, True, True, 0)
        tt_hbox.pack_end(tt_switch, False, False, 0)
        vbox.pack_start(tt_hbox, False, False, 0)

        # --- Per-Tab Venv Settings ---
        current_tab_index = self.notebook.get_current_page()
        current_tab_venv_settings = self.tab_venv_settings.get(current_tab_index, {})
        use_custom_venv_tab = current_tab_venv_settings.get("use_custom_venv", False)
        venv_folder_tab = current_tab_venv_settings.get("venv_folder", "")

        # Use custom venv (per-tab)
        cv_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cv_label = Gtk.Label(label="Use Custom Venv (Tab-Specific):", xalign=0.0)
        cv_switch = Gtk.Switch(active=use_custom_venv_tab)  # Use per-tab setting
        cv_hbox.pack_start(cv_label, True, True, 0)
        cv_hbox.pack_end(cv_switch, False, False, 0)
        vbox.pack_start(cv_hbox, False, False, 0)

        # Venv path (per-tab)
        vp_outer_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        vp_label = Gtk.Label(label="Venv Path (Tab-Specific):", xalign=0.0)
        vp_controls_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vp_entry = Gtk.Entry(
            text=venv_folder_tab,  # Use per-tab setting
            sensitive=cv_switch.get_active(),
            xalign=0.0,
        )
        vp_button = Gtk.Button(label="Browse...", sensitive=cv_switch.get_active())
        vp_controls_hbox.pack_start(vp_entry, True, True, 0)
        vp_controls_hbox.pack_start(vp_button, False, False, 0)
        vp_outer_hbox.pack_start(vp_label, False, False, 0)
        vp_outer_hbox.pack_start(vp_controls_hbox, True, True, 0)
        vbox.pack_start(vp_outer_hbox, False, False, 0)

        def _toggle_venv_widgets(switch, *args):
            is_active = switch.get_active()
            vp_entry.set_sensitive(is_active)
            vp_button.set_sensitive(is_active)

        cv_switch.connect("notify::active", _toggle_venv_widgets)
        _toggle_venv_widgets(cv_switch)

        def _browse_venv(button):
            folder_dialog = Gtk.FileChooserDialog(
                title="Select Venv Folder",
                parent=dialog,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            folder_dialog.add_buttons(
                Gtk.STOCK_CANCEL,
                Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OPEN,
                Gtk.ResponseType.OK,
            )
            current_path = vp_entry.get_text()
            if current_path and os.path.isdir(current_path):
                folder_dialog.set_current_folder(current_path)
            browse_response = folder_dialog.run()
            if browse_response == Gtk.ResponseType.OK:
                venv_folder = folder_dialog.get_filename()
                vp_entry.set_text(venv_folder)
            folder_dialog.destroy()

        vp_button.connect("clicked", _browse_venv)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            active_scheme_id = cs_combo.get_active_id()
            if active_scheme_id:
                self.settings.set_string(SETTING_COLOR_SCHEME_ID, active_scheme_id)

            self.settings.set_boolean(SETTING_DRAW_WHITESPACES, dw_switch.get_active())
            self.settings.set_int(SETTING_TAB_SIZE, ts_spin.get_value_as_int())
            self.settings.set_boolean(SETTING_TRANSLATE_TABS, tt_switch.get_active())

            # Save per-tab venv settings
            self.tab_venv_settings[current_tab_index] = {
                "use_custom_venv": cv_switch.get_active(),
                "venv_folder": vp_entry.get_text(),
            }
            self.update_python_env_status()  # Update status after settings change

        dialog.destroy()

    def on_settings_changed(self, settings, key):
        """Applies settings when they change via GSettings."""
        print(f"Settings changed signal received for key: {key}")
        self.apply_settings()
        # For now, update python env status on any setting change, as it's simple.
        # In future, optimize to check if venv settings are changed.
        self.update_python_env_status()

    def on_new_tab_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the New Tab action, triggered by hotkey (Ctrl+N)."""
        self._add_new_tab()
        self._set_status_message(
            "New Tab Added",
            temporary=True,
            temporary_source_view=(
                self._get_current_tab_widgets()["code_input"]
                if self._get_current_tab_widgets()
                else None
            ),
        )

    def on_show_hotkeys(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Displays a list of available hotkeys in the output window."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return

        output_buffer = tab_widgets["output_buffer"]
        output_view = tab_widgets["output_view"]
        code_input = tab_widgets["code_input"]

        hotkey_list = """
    --- Hotkeys ---
    Ctrl+R: Run Code
    Ctrl+C: Copy Code
    Ctrl+S: Export Code to File
    Ctrl+T or Ctrl+,: Open Settings
    Ctrl+H: Show Hotkeys (this list)
    Ctrl+N: New Tab
    Ctrl+W: Remove Tab
    Ctrl+Shift+P: Pip Freeze
    ---
    """
        output_buffer.set_text(hotkey_list)
        start_iter = output_buffer.get_start_iter()
        output_view.scroll_to_iter(start_iter, 0.0, False, 0.0, 0.0)
        self._set_status_message(
            "Hotkeys list displayed in output.",
            temporary=True,
            temporary_source_view=code_input,
        )

    def apply_settings(self):
        """Applies current application-wide settings values to the UI (all tabs)."""
        print("Applying application-wide settings...")

        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme_id = self.settings.get_string(SETTING_COLOR_SCHEME_ID)
        scheme = style_manager.get_scheme(scheme_id)

        if not scheme:
            print(
                f"Warning: Saved scheme '{scheme_id}' not found. Falling back.",
                file=sys.stderr,
            )
            scheme_id = DEFAULT_STYLE_SCHEME
            scheme = style_manager.get_scheme(scheme_id)
            if not scheme:
                scheme_id = "classic"
                scheme = style_manager.get_scheme(scheme_id)

        if scheme:
            # Apply color scheme to ALL code buffers (all tabs for now)
            for page_num in range(self.notebook.get_n_pages()):
                paned = self.notebook.get_nth_page(page_num)
                if paned and hasattr(paned, "tab_widgets"):  # Safety check
                    code_buffer = paned.tab_widgets["code_buffer"]
                    code_buffer.set_style_scheme(scheme)
            print(f"Applied color scheme: {scheme_id} to all tabs")
        else:
            print(
                f"Warning: Could not apply any color scheme (tried: {self.settings.get_string(SETTING_COLOR_SCHEME_ID)}, {DEFAULT_STYLE_SCHEME}, classic).",
                file=sys.stderr,
            )

        if self.settings.get_boolean(SETTING_DRAW_WHITESPACES):
            space_drawer_types = (
                GtkSource.SpaceTypeFlags.SPACE | GtkSource.SpaceTypeFlags.TAB
            )
        else:
            space_drawer_types = 0
        # Apply draw whitespaces to ALL code inputs (all tabs)
        for page_num in range(self.notebook.get_n_pages()):
            paned = self.notebook.get_nth_page(page_num)
            if paned and hasattr(paned, "tab_widgets"):  # Safety check
                space_drawer = paned.tab_widgets["space_drawer"]
                space_drawer.set_types_for_locations(
                    GtkSource.SpaceLocationFlags.ALL, space_drawer_types
                )
        print(
            f"Applied draw whitespaces: {self.settings.get_boolean(SETTING_DRAW_WHITESPACES)} to all tabs"
        )

        tab_size = self.settings.get_int(SETTING_TAB_SIZE)
        translate_tabs = self.settings.get_boolean(SETTING_TRANSLATE_TABS)
        # Apply tab settings to ALL code inputs (all tabs)
        for page_num in range(self.notebook.get_n_pages()):
            paned = self.notebook.get_nth_page(page_num)
            if paned and hasattr(paned, "tab_widgets"):  # Safety check
                code_input = paned.tab_widgets["code_input"]
                code_input.set_tab_width(tab_size)
                code_input.set_insert_spaces_instead_of_tabs(translate_tabs)
        print(
            f"Applied tab size: {tab_size}, translate tabs: {translate_tabs} to all tabs"
        )

        # Refresh views in all tabs
        for page_num in range(self.notebook.get_n_pages()):
            paned = self.notebook.get_nth_page(page_num)
            if paned and hasattr(paned, "tab_widgets"):  # Safety check
                code_input = paned.tab_widgets["code_input"]
                code_input.queue_draw()
        print("Application-wide settings applied to all tabs.")

    def get_python_interpreter(self):
        """Determines the Python interpreter path based on per-tab settings if available, otherwise application-wide settings."""
        current_tab_index = self.notebook.get_current_page()
        tab_venv_settings = self.tab_venv_settings.get(current_tab_index, {})
        use_custom_venv = tab_venv_settings.get(
            "use_custom_venv", False
        )  # Per-tab setting
        venv_folder = tab_venv_settings.get("venv_folder", "")  # Per-tab setting

        if use_custom_venv:
            if venv_folder and os.path.isdir(venv_folder):
                python_executable = os.path.join(venv_folder, "bin", "python")
                python3_executable = os.path.join(venv_folder, "bin", "python3")
                if os.path.exists(python3_executable):
                    return python3_executable
                elif os.path.exists(python_executable):
                    return python_executable
                else:
                    print(
                        f"Warning: No 'python' or 'python3' found in {os.path.join(venv_folder, 'bin')}. Falling back to system.",
                        file=sys.stderr,
                    )
                    pass
            else:
                print(
                    f"Warning: Custom venv path '{venv_folder}' is invalid. Falling back to system.",
                    file=sys.stderr,
                )
                pass  # Fall through to system python

        # Default to system python3 or python
        try:
            subprocess.run(
                ["python3", "--version"], check=True, capture_output=True, text=True
            )
            return "/usr/bin/python3"
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                subprocess.run(
                    ["python", "--version"], check=True, capture_output=True, text=True
                )
                return "/usr/bin/python"
            except (FileNotFoundError, subprocess.CalledProcessError):
                print(
                    "Warning: Neither 'python3' nor 'python' found in PATH.",
                    file=sys.stderr,
                )
                return "Warning: Neither 'python3' nor 'python' found in PATH."

    def update_python_env_status(self, source_view=None):
        """Updates the status bar with the current Python environment info for the current tab."""
        python_interpreter = self.get_python_interpreter()
        python_version = "Unknown"

        try:
            result = subprocess.run(
                [python_interpreter, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            if result.returncode == 0:
                version_output = result.stdout.strip() or result.stderr.strip()
                if version_output.startswith("Python "):
                    python_version = version_output.split()[1]
                else:
                    python_version = version_output
            else:
                print(
                    f"Warning: Failed to get version from '{python_interpreter}'. stderr: {result.stderr}",
                    file=sys.stderr,
                )
                python_version = "Version N/A"  # Indicate version not available, but still show path if custom venv

        except FileNotFoundError:
            python_version = "Not Found"
            python_interpreter = (
                "python"  # Fallback for display purposes if path is invalid
            )
        except subprocess.TimeoutExpired:
            python_version = "Timeout"
        except Exception as e:
            print(f"Error checking Python version: {e}", file=sys.stderr)
            python_version = "Error"

        status_text = (
            f"{python_interpreter} ({python_version})"  # Show path + version
        )
        self._set_status_message(status_text)

    def on_tab_switched(self, notebook, page, page_num):
        """Handler for tab switch event. Updates status bar."""
        self.update_python_env_status()  # Update status when tab is switched

    def _set_status_message(self, text, temporary=False, temporary_source_view=None, timeout=STATUS_MESSAGE_TIMEOUT_MS):
        """Updates the status bar label, optionally resetting after a delay."""
        if self._status_timeout_id:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None

        self.status_label.set_text(text)

        if temporary:
            self._status_timeout_id = GLib.timeout_add(
                timeout,
                lambda: self._restore_default_status(temporary_source_view),
            )

    def _restore_default_status(self, source_view=None):
        """Restores the status bar to the default (Python env info). Called by timeout."""
        self.update_python_env_status(source_view)
        self._status_timeout_id = None
        return GLib.SOURCE_REMOVE

    def on_remove_tab_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the Remove Tab action, triggered by hotkey (Ctrl+W)."""
        current_page_index = self.notebook.get_current_page()
        if (
            current_page_index != -1 and self.notebook.get_n_pages() > 1
        ):  # Ensure not removing last tab
            self.notebook.remove_page(current_page_index)
            self._set_status_message(
                "Tab removed.",
                temporary=True,
                temporary_source_view=(
                    self._get_current_tab_widgets()["code_input"]
                    if self._get_current_tab_widgets()
                    else None
                ),
            )
        elif self.notebook.get_n_pages() <= 1:
            self._set_status_message(
                "Cannot remove the last tab.",
                temporary=True,
                temporary_source_view=(
                    self._get_current_tab_widgets()["code_input"]
                    if self._get_current_tab_widgets()
                    else None
                ),
            )

    def on_pip_freeze_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the Pip Freeze action, triggered by hotkey (Ctrl+Shift+P)."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return

        output_buffer = tab_widgets["output_buffer"]
        output_view = tab_widgets["output_view"]
        code_input = tab_widgets["code_input"]

        python_interpreter = self.get_python_interpreter()

        self._set_status_message(
            f"Running pip freeze with {os.path.basename(python_interpreter)}...",
            temporary_source_view=code_input,
        )
        output_buffer.set_text("")  # Clear output before pip freeze

        thread = threading.Thread(
            target=self._run_pip_freeze_thread,
            args=(python_interpreter, output_buffer, output_view, code_input),
        )
        thread.daemon = True
        thread.start()

    def _run_pip_freeze_thread(
        self, python_interpreter, output_buffer, output_view, source_view
    ):
        """Worker thread function to execute pip freeze."""
        output = ""
        error = ""
        success = False
        try:
            result = subprocess.run(
                [
                    python_interpreter,
                    "-m",
                    "pip",
                    "freeze",
                ],  # Use -m pip to ensure correct pip is used
                capture_output=True,
                text=True,
                check=False,  # Don't raise exception if pip freeze fails (e.g., in a non-venv)
                timeout=EXECUTION_TIMEOUT,
            )
            if result.returncode == 0:
                output = result.stdout
                success = True
            else:
                error = f"Error running pip freeze (Exit Code: {result.returncode}):\n{result.stderr}"
                output = result.stdout  # Still show stdout if available
        except FileNotFoundError:
            error = f"Error: pip not found for interpreter '{python_interpreter}'"
        except subprocess.TimeoutExpired:
            error = "Error: pip freeze timed out"
        except Exception as e:
            error = f"Error executing pip freeze: {e}"

        GLib.idle_add(
            self._update_output_view,
            output,
            error,
            success,
            output_buffer,
            output_view,
            source_view,
        )


def main():
    PythonRunnerApp()
    Gtk.main()


if __name__ == "__main__":
    main()
