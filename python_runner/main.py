#!/usr/bin/env python3

import os
import subprocess
import threading
import sys
import json

import gi

from python_runner.version import VERSION

gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "3.0")

from gi.repository import Gtk, Gio, Pango, GtkSource, Gdk, GLib

# --- Constants ---
APP_ID = "com.example.python-runner"
SETTINGS_SCHEMA = APP_ID
INITIAL_WIDTH, INITIAL_HEIGHT = 700, 500
DEFAULT_STYLE_SCHEME = "oblivion"
STATUS_MESSAGE_TIMEOUT_MS = 2000  # 2 seconds (Used for non-execution messages)
DEFAULT_TAB_SIZE = 4
DEFAULT_TRANSLATE_TABS = True

# Settings Keys
SETTING_DRAW_WHITESPACES = "draw-whitespaces"
SETTING_TAB_SIZE = "tab-size"
SETTING_TRANSLATE_TABS = "translate-tabs"
SETTING_COLOR_SCHEME_ID = "color-scheme-id"
CACHE_FILE_NAME = "python_runner_cache.json"
EXECUTION_TIMEOUT = 30
# --- End Constants ---

# --- Default Venv Settings ---
DEFAULT_VENV_SETTINGS = {
    "use_custom_venv": False,
    "venv_folder": "",
}
# --- End Default Venv Settings ---


class PythonRunnerApp(Gtk.Window):
    """
    A simple GTK application to write and run Python code snippets with tabs,
    saving/loading all tabs, and per-tab venv settings.
    """

    def __init__(self):
        Gtk.Window.__init__(self, title=f"Python Runner {VERSION}")

        self.set_default_size(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_size_request(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", self.on_destroy)
        self._status_timeout_id = None

        self._setup_settings()
        self._setup_css()
        self.cache_file_path = self._get_cache_file_path()
        self._setup_ui()
        self._setup_hotkeys()

        cache_loaded = self._load_code_from_cache()
        if not cache_loaded:
            print("Cache not loaded, creating a default tab.")
            self._add_new_tab(add_empty=True)  # Add an empty default tab

        self.apply_settings()
        self.update_python_env_status()
        # self.on_show_hotkeys() # Optionally show hotkeys on startup

        self.show_all()

    def on_destroy(self, _):
        """Save cache *before* quitting."""
        saved = self._save_code_to_cache()
        if not saved:
            print("ERROR: Failed to save cache on exit!", file=sys.stderr)
        Gtk.main_quit()

    def _get_cache_file_path(self):
        """Gets the path to the cache file in the user's cache directory."""
        cache_dir = GLib.get_user_cache_dir()
        if not cache_dir:
            cache_dir = os.path.abspath(".")
            print(
                f"Warning: User cache directory not found. Using '{cache_dir}'.",
                file=sys.stderr,
            )
            app_cache_dir = cache_dir  # Use current dir if cache dir fails
        else:
            app_cache_dir = os.path.join(
                cache_dir, APP_ID
            )  # Use app-specific subfolder

        os.makedirs(
            app_cache_dir, exist_ok=True
        )  # Ensure directory exists regardless of source
        return os.path.join(app_cache_dir, CACHE_FILE_NAME)

    def _save_code_to_cache(self):
        """Saves the code and venv settings from *all* tabs to the JSON cache file."""
        tabs_data = []
        n_pages = self.notebook.get_n_pages()
        if n_pages == 0:
            print("No tabs to save.")
            # Save an empty list for consistency.
            pass

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
            cache_dir = os.path.dirname(self.cache_file_path)
            # Directory creation is handled in _get_cache_file_path, but check again just in case
            os.makedirs(cache_dir, exist_ok=True)
            with open(self.cache_file_path, "w", encoding="utf-8") as f:
                json.dump(tabs_data, f, indent=4)  # Use json.dump with indentation
            print(
                f"Code for {len(tabs_data)} tabs saved to cache {self.cache_file_path}"
            )
            return True
        except Exception as e:
            print(f"Error saving to cache: {e}", file=sys.stderr)
            return False

    def _load_code_from_cache(self):
        """Loads code and venv settings from the JSON cache file, creating tabs."""
        if not os.path.exists(self.cache_file_path):
            print("Cache file not found.")
            return False

        try:
            with open(self.cache_file_path, "r", encoding="utf-8") as f:
                tabs_data = json.load(f)

            if not isinstance(tabs_data, list):
                print(
                    "Error: Cache file format is invalid (expected a list).",
                    file=sys.stderr,
                )
                return False

            if not tabs_data:
                print("Cache file is empty.")
                return False

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
                        f"Warning: Invalid venv_settings format for tab {i+1}. Using defaults.",
                        file=sys.stderr,
                    )
                    venv_settings = DEFAULT_VENV_SETTINGS.copy()

                self._add_tab_with_content(code, venv_settings)

            print(f"Loaded {len(tabs_data)} tabs from cache.")
            self._set_status_message("Code loaded from cache.", temporary=True)
            self.notebook.set_current_page(0)
            return True

        except json.JSONDecodeError as e:
            print(
                f"Error decoding cache file ({self.cache_file_path}): {e}",
                file=sys.stderr,
            )
            return False
        except Exception as e:
            print(f"Error loading from cache: {e}", file=sys.stderr)
            self._set_status_message("Error loading code from cache.", temporary=True)
            return False

    def _setup_settings(self):
        """Initializes GSettings."""
        schema_source = Gio.SettingsSchemaSource.get_default()
        schema_id_to_use = SETTINGS_SCHEMA
        gsettings_path = "/"  # Default path

        if not schema_source:
            print(
                "Warning: Default GSettings schema source not found. Using defaults.",
                file=sys.stderr,
            )
            schema_id_to_use = "dummy.schema.nonexistent"
            gsettings_path = "/dev/null/"
            self.settings = Gio.Settings.new_with_path(schema_id_to_use, gsettings_path)
            return

        schema = schema_source.lookup(schema_id_to_use, True)
        if not schema:
            print(
                f"Warning: GSettings schema '{schema_id_to_use}' not found. Using defaults.",
                file=sys.stderr,
            )
            schema_id_to_use = "dummy.schema.nonexistent"
            gsettings_path = "/dev/null/"
            self.settings = Gio.Settings.new_with_path(schema_id_to_use, gsettings_path)
        else:
            print(f"GSettings schema '{schema_id_to_use}' found.")
            self.settings = Gio.Settings.new(schema_id_to_use)
            self.settings.connect("changed", self.on_settings_changed)

    def _setup_css(self):
        """Loads and applies CSS styles."""
        css_provider = Gtk.CssProvider()
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
                current_paned = self.notebook.get_nth_page(
                    self.notebook.get_current_page()
                )
                if current_paned and hasattr(current_paned, "venv_settings"):
                    initial_venv_settings = current_paned.venv_settings.copy()

        code = ""  # Default empty code for new tabs
        self._add_tab_with_content(code, initial_venv_settings)

    def _add_tab_with_content(self, code, venv_settings):
        """Adds a new tab with the given code and venv_settings."""
        tab_content_paned = self._create_tab_content(venv_settings)

        code_buffer = tab_content_paned.tab_widgets["code_buffer"]
        code_buffer.set_text(code or "", -1)

        tab_label = Gtk.Label(label=f"Tab {self.notebook.get_n_pages() + 1}")

        self.notebook.append_page(tab_content_paned, tab_label)
        self.notebook.show_all()
        new_page_index = self.notebook.get_n_pages() - 1
        self.notebook.set_current_page(new_page_index)

        self.apply_global_settings_to_tab(new_page_index)
        self.update_python_env_status()

    def _create_tab_content(self, initial_venv_settings):
        """Creates the content (Paned with code and output views) for a single tab."""
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)

        paned.venv_settings = initial_venv_settings.copy()
        paned.tab_widgets = {}

        code_input = GtkSource.View()
        code_buffer = GtkSource.Buffer()
        code_input.set_buffer(code_buffer)

        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = style_manager.get_scheme(DEFAULT_STYLE_SCHEME)
        if scheme:
            code_buffer.set_style_scheme(scheme)
        lang_manager = GtkSource.LanguageManager.get_default()
        python_lang = lang_manager.get_language("python3") or lang_manager.get_language(
            "python"
        )
        if python_lang:
            code_buffer.set_language(python_lang)

        code_input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        code_input.set_monospace(True)
        code_input.set_show_line_numbers(True)
        code_input.set_highlight_current_line(True)
        code_input.set_auto_indent(True)
        code_input.set_indent_on_tab(True)
        code_input.set_tab_width(DEFAULT_TAB_SIZE)
        code_input.set_insert_spaces_instead_of_tabs(DEFAULT_TRANSLATE_TABS)

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

        paned.tab_widgets = {
            "code_input": code_input,
            "code_buffer": code_buffer,
            "output_buffer": output_buffer,
            "output_view": output_view,
            "space_drawer": space_drawer,
            "paned": paned,
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

        return status_box

    def _setup_hotkeys(self):
        """Sets up global hotkeys for application actions."""
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)

        key, mod = Gtk.accelerator_parse("<Control>R")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_run_clicked)
        key, mod = Gtk.accelerator_parse("<Control>C")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_copy_clicked)
        key, mod = Gtk.accelerator_parse("<Control>S")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_export_clicked)
        key, mod = Gtk.accelerator_parse("<Control>T")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_settings_clicked)
        key, mod = Gtk.accelerator_parse("<Control>comma")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_settings_clicked)
        key, mod = Gtk.accelerator_parse("<Control>H")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_show_hotkeys)
        key, mod = Gtk.accelerator_parse("<Control>N")
        accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, self.on_new_tab_clicked)
        key, mod = Gtk.accelerator_parse("<Control>W")
        accel_group.connect(
            key, mod, Gtk.AccelFlags.VISIBLE, self.on_remove_tab_clicked
        )
        key, mod = Gtk.accelerator_parse("<Control>P")
        accel_group.connect(
            key, mod, Gtk.AccelFlags.VISIBLE, self.on_pip_freeze_clicked
        )

    def _get_current_tab_widgets(self):
        """Gets the widgets associated with the current tab's Paned."""
        current_page_index = self.notebook.get_current_page()
        if current_page_index == -1:
            return None
        paned = self.notebook.get_nth_page(current_page_index)
        if paned and hasattr(paned, "tab_widgets"):
            return paned.tab_widgets
        else:
            print(
                f"Warning: Could not find widgets for current tab index {current_page_index}",
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
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            stdout_data, stderr_data = process.communicate(timeout=EXECUTION_TIMEOUT)

            if process.returncode == 0:
                output = stdout_data
                if stderr_data:
                    error = f"--- Warnings/Stderr Output ---\n{stderr_data}"
                success = True
            else:
                output = stdout_data
                error = f"--- Error (Exit Code {process.returncode}) ---\n{stderr_data}"

        except FileNotFoundError:
            error = f"Error: Python interpreter '{python_interpreter}' not found."
            success = False
        except subprocess.TimeoutExpired:
            if process:
                process.kill()
                stdout_data, stderr_data = process.communicate()
                output = stdout_data
                error = f"--- Error: Code execution timed out after {EXECUTION_TIMEOUT} seconds ---\n{stderr_data}"
            else:
                error = f"Error: Code execution timed out after {EXECUTION_TIMEOUT} seconds."
            success = False
        except Exception as e:
            error = f"Error executing code: {e}"
            success = False
            if process and process.poll() is None:
                process.kill()

        GLib.idle_add(
            self._update_output_view,
            output,
            error,
            success,
            output_buffer,
            output_view,
            source_view,
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
        end_iter = output_buffer.get_end_iter()
        output_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 0.0)

        # --- REMOVED "Finished/Failed" status update ---
        # Instead, directly restore the default status if the originating tab is still active
        current_widgets = self._get_current_tab_widgets()
        active_source_view = current_widgets["code_input"] if current_widgets else None
        if (
            source_view == active_source_view
        ):  # Only restore status if the originating tab is still active
            self._restore_default_status()
        # ---------------------------------------------

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
        if "Warning:" in python_interpreter or "Not Found" in python_interpreter:
            self._set_status_message(
                f"Error: Invalid Python interpreter selected ({python_interpreter}). Check settings.",
                temporary=False,
            )
            return

        self._set_status_message(
            f"Running with {os.path.basename(python_interpreter)}...",
            temporary=False,  # Keep this status until execution finishes or changes
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
            ),
            daemon=True,
        )
        thread.start()

    def on_copy_clicked(self, *args):
        """Handles the Copy action, triggered by hotkey (Ctrl+C), on the current tab."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            return

        code_buffer = tab_widgets["code_buffer"]
        code_input = tab_widgets["code_input"]

        if code_buffer.get_has_selection():
            start, end = code_buffer.get_selection_bounds()
            code = code_buffer.get_text(start, end, True)
        else:
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

    def on_export_clicked(self, *args):
        """Handles the Export action (Save As), triggered by hotkey (Ctrl+S), on the current tab."""
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
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE,
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
            selected_filter = dialog.get_filter()
            if selected_filter == py_filter and not filename.lower().endswith(".py"):
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
                print(f"Error saving file '{filename}': {e}", file=sys.stderr)
                error_dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Error Saving File",
                )
                error_dialog.format_secondary_text(f"Could not save file:\n{e}")
                error_dialog.run()
                error_dialog.destroy()
                self._set_status_message(
                    f"Error saving file",
                    temporary=True,
                    temporary_source_view=code_input,
                )
        elif response == Gtk.ResponseType.CANCEL:
            self._set_status_message(
                f"Export cancelled",
                temporary=True,
                temporary_source_view=code_input,
            )

        dialog.destroy()

    def on_settings_clicked(self, *args):
        """Shows the settings dialog, including per-tab venv options."""
        current_tab_index = self.notebook.get_current_page()
        current_paned = (
            self.notebook.get_nth_page(current_tab_index)
            if current_tab_index != -1
            else None
        )

        if not current_paned or not hasattr(current_paned, "venv_settings"):
            print(
                "Warning: Could not get current tab's venv settings for dialog.",
                file=sys.stderr,
            )
            current_tab_venv_settings = DEFAULT_VENV_SETTINGS.copy()
        else:
            current_tab_venv_settings = current_paned.venv_settings

        dialog = Gtk.Dialog(
            title="Settings",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_APPLY,
            Gtk.ResponseType.APPLY,
            Gtk.STOCK_OK,
            Gtk.ResponseType.OK,
        )
        dialog.set_resizable(False)
        dialog.set_default_response(Gtk.ResponseType.OK)

        content_area = dialog.get_content_area()
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=12)
        content_area.add(main_vbox)

        global_frame = Gtk.Frame(label="Global Editor Settings")
        main_vbox.pack_start(global_frame, False, False, 0)
        global_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        global_frame.add(global_vbox)

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
            schemes_data.sort(key=lambda x: x["name"].lower())

        dw_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dw_label = Gtk.Label(label="Draw Whitespaces:", xalign=0.0)
        dw_switch = Gtk.Switch(
            active=self.settings.get_boolean(SETTING_DRAW_WHITESPACES)
        )
        dw_hbox.pack_start(dw_label, True, True, 0)
        dw_hbox.pack_end(dw_switch, False, False, 0)
        global_vbox.pack_start(dw_hbox, False, False, 0)

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
        cs_combo.set_size_request(150, -1)
        cs_hbox.pack_start(cs_label, False, False, 0)
        cs_hbox.pack_start(cs_combo, True, True, 0)
        global_vbox.pack_start(cs_hbox, False, False, 0)

        ts_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ts_label = Gtk.Label(label="Tab Size (Spaces):", xalign=0.0)
        ts_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
        ts_spin.set_value(self.settings.get_int(SETTING_TAB_SIZE))
        ts_hbox.pack_start(ts_label, True, True, 0)
        ts_hbox.pack_end(ts_spin, False, False, 0)
        global_vbox.pack_start(ts_hbox, False, False, 0)

        tt_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tt_label = Gtk.Label(label="Use Spaces Instead of Tabs:", xalign=0.0)
        tt_switch = Gtk.Switch(active=self.settings.get_boolean(SETTING_TRANSLATE_TABS))
        tt_hbox.pack_start(tt_label, True, True, 0)
        tt_hbox.pack_end(tt_switch, False, False, 0)
        global_vbox.pack_start(tt_hbox, False, False, 0)

        tab_frame = Gtk.Frame(label="Current Tab Python Environment")
        main_vbox.pack_start(tab_frame, False, False, 0)
        tab_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        tab_frame.add(tab_vbox)

        use_custom_venv_tab = current_tab_venv_settings.get("use_custom_venv", False)
        venv_folder_tab = current_tab_venv_settings.get("venv_folder", "")

        cv_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cv_label = Gtk.Label(label="Use Custom Virtual Environment:", xalign=0.0)
        cv_switch = Gtk.Switch(active=use_custom_venv_tab)
        cv_hbox.pack_start(cv_label, True, True, 0)
        cv_hbox.pack_end(cv_switch, False, False, 0)
        tab_vbox.pack_start(cv_hbox, False, False, 0)

        vp_outer_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        vp_label = Gtk.Label(label="Venv Path:", xalign=0.0)
        vp_controls_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vp_entry = Gtk.Entry(
            text=venv_folder_tab,
            sensitive=cv_switch.get_active(),
            xalign=0.0,
            placeholder_text="Path to venv directory (e.g., /path/to/myenv)",
        )
        vp_button = Gtk.Button(label="Browse...", sensitive=cv_switch.get_active())
        vp_controls_hbox.pack_start(vp_entry, True, True, 0)
        vp_controls_hbox.pack_start(vp_button, False, False, 0)
        vp_outer_hbox.pack_start(vp_label, False, False, 0)
        vp_outer_hbox.pack_start(vp_controls_hbox, True, True, 0)
        tab_vbox.pack_start(vp_outer_hbox, False, False, 0)

        def _toggle_venv_widgets(switch, *args):
            is_active = switch.get_active()
            vp_entry.set_sensitive(is_active)
            vp_button.set_sensitive(is_active)

        cv_switch.connect("notify::active", _toggle_venv_widgets)

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
                try:
                    folder_dialog.set_current_folder(current_path)
                except GLib.Error as e:
                    print(
                        f"Warning: Could not set folder dialog path to '{current_path}': {e}",
                        file=sys.stderr,
                    )
            elif os.path.isdir(os.path.expanduser("~")):
                folder_dialog.set_current_folder(os.path.expanduser("~"))

            browse_response = folder_dialog.run()
            if browse_response == Gtk.ResponseType.OK:
                venv_folder = folder_dialog.get_filename()
                vp_entry.set_text(venv_folder)
            folder_dialog.destroy()

        vp_button.connect("clicked", _browse_venv)

        def _apply_changes():
            print("Applying settings changes...")
            active_scheme_id = cs_combo.get_active_id()
            if active_scheme_id:
                self.settings.set_string(SETTING_COLOR_SCHEME_ID, active_scheme_id)

            self.settings.set_boolean(SETTING_DRAW_WHITESPACES, dw_switch.get_active())
            self.settings.set_int(SETTING_TAB_SIZE, ts_spin.get_value_as_int())
            self.settings.set_boolean(SETTING_TRANSLATE_TABS, tt_switch.get_active())

            if current_paned and hasattr(current_paned, "venv_settings"):
                current_paned.venv_settings["use_custom_venv"] = cv_switch.get_active()
                current_paned.venv_settings["venv_folder"] = vp_entry.get_text()
                print(
                    f"Applied venv settings for tab {current_tab_index}: {current_paned.venv_settings}"
                )
                self.update_python_env_status()
            else:
                print(
                    "Warning: Could not apply venv settings (no valid tab found).",
                    file=sys.stderr,
                )

            self.apply_settings()
            self._set_status_message("Settings applied.", temporary=True)

        dialog.show_all()
        while True:
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                _apply_changes()
                break
            elif response == Gtk.ResponseType.APPLY:
                _apply_changes()
            elif (
                response == Gtk.ResponseType.CANCEL
                or response == Gtk.ResponseType.DELETE_EVENT
            ):
                self.update_python_env_status()
                self._set_status_message("Settings cancelled.", temporary=True)
                break

        dialog.destroy()

    def on_settings_changed(self, settings, key):
        """Applies settings when they change via GSettings."""
        print(f"Settings changed signal received for key: {key}")
        self.apply_settings()

    def on_new_tab_clicked(self, *args):
        """Handles the New Tab action, triggered by hotkey (Ctrl+N)."""
        self._add_new_tab(inherit_settings=True)
        self._set_status_message(
            "New Tab Added",
            temporary=True,
            temporary_source_view=(
                self._get_current_tab_widgets()["code_input"]
                if self._get_current_tab_widgets()
                else None
            ),
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
Ctrl+R  : Run Code
Ctrl+C  : Copy Code/Selection
Ctrl+S  : Export Code to File...
Ctrl+T  : Open Settings
Ctrl+H  : Show Hotkeys (this list)
Ctrl+N  : New Tab
Ctrl+W  : Remove Current Tab
Ctrl+P  : Pip Freeze (current env)
"""
        if output_buffer and output_view:
            output_buffer.set_text(hotkey_list)
            start_iter = output_buffer.get_start_iter()
            output_view.scroll_to_iter(start_iter, 0.0, False, 0.0, 0.0)
        else:
            print(
                "Warning: Could not display hotkeys, output view not found.",
                file=sys.stderr,
            )
            self._set_status_message("Error displaying hotkeys.", temporary=True)

    def apply_settings(self):
        """Applies current application-wide (GSettings) values to the UI of *all* tabs."""
        print("Applying application-wide settings to all tabs...")
        n_pages = self.notebook.get_n_pages()
        for i in range(n_pages):
            self.apply_global_settings_to_tab(i)

        print(f"Application-wide settings applied to {n_pages} tabs.")

    def apply_global_settings_to_tab(self, page_index):
        """Applies global editor settings (theme, whitespace, tabs) to a specific tab."""
        paned = self.notebook.get_nth_page(page_index)
        if not paned or not hasattr(paned, "tab_widgets"):
            print(
                f"Warning: Cannot apply settings, invalid widgets for tab {page_index}",
                file=sys.stderr,
            )
            return

        tab_widgets = paned.tab_widgets
        code_input = tab_widgets["code_input"]
        code_buffer = tab_widgets["code_buffer"]
        space_drawer = tab_widgets["space_drawer"]

        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme_id = self.settings.get_string(SETTING_COLOR_SCHEME_ID)
        scheme = style_manager.get_scheme(scheme_id)
        if not scheme:
            print(
                f"Warning: Scheme '{scheme_id}' not found. Falling back to default.",
                file=sys.stderr,
            )
            scheme_id = DEFAULT_STYLE_SCHEME
            scheme = style_manager.get_scheme(scheme_id)
        if not scheme:
            scheme_id = "classic"
            scheme = style_manager.get_scheme(scheme_id)

        if scheme:
            if code_buffer.get_style_scheme() != scheme:
                code_buffer.set_style_scheme(scheme)
        else:
            print(
                f"Warning: Could not apply any color scheme to tab {page_index}.",
                file=sys.stderr,
            )

        draw_whitespaces = self.settings.get_boolean(SETTING_DRAW_WHITESPACES)
        # Use flags known to be available in most GtkSourceView 3 versions
        required_types = (
            (
                GtkSource.SpaceTypeFlags.SPACE
                | GtkSource.SpaceTypeFlags.TAB
                | GtkSource.SpaceTypeFlags.NEWLINE
            )
            if draw_whitespaces
            else GtkSource.SpaceTypeFlags.NONE
        )

        current_types = space_drawer.get_types_for_locations(
            GtkSource.SpaceLocationFlags.ALL
        )
        if current_types != required_types:
            space_drawer.set_types_for_locations(
                GtkSource.SpaceLocationFlags.ALL, required_types
            )

        tab_size = self.settings.get_int(SETTING_TAB_SIZE)
        translate_tabs = self.settings.get_boolean(SETTING_TRANSLATE_TABS)
        if code_input.get_tab_width() != tab_size:
            code_input.set_tab_width(tab_size)
        if code_input.get_insert_spaces_instead_of_tabs() != translate_tabs:
            code_input.set_insert_spaces_instead_of_tabs(translate_tabs)

        code_input.queue_draw()

    def get_python_interpreter(self):
        """Determines the Python interpreter path based on the *current* tab's venv settings."""
        current_tab_index = self.notebook.get_current_page()
        if current_tab_index == -1:
            print(
                "Warning: No active tab selected, cannot determine Python interpreter.",
                file=sys.stderr,
            )
            return "Warning: No active tab"

        current_paned = self.notebook.get_nth_page(current_tab_index)
        if not current_paned or not hasattr(current_paned, "venv_settings"):
            print(
                f"Warning: Could not get venv settings for current tab index {current_tab_index}. Falling back.",
                file=sys.stderr,
            )
            tab_venv_settings = DEFAULT_VENV_SETTINGS.copy()
        else:
            tab_venv_settings = current_paned.venv_settings

        use_custom_venv = tab_venv_settings.get("use_custom_venv", False)
        venv_folder = tab_venv_settings.get("venv_folder", "")

        if use_custom_venv:
            if venv_folder and os.path.isdir(venv_folder):
                python3_executable = os.path.join(venv_folder, "bin", "python3")
                if os.path.isfile(python3_executable) and os.access(
                    python3_executable, os.X_OK
                ):
                    return python3_executable

                python_executable = os.path.join(venv_folder, "bin", "python")
                if os.path.isfile(python_executable) and os.access(
                    python_executable, os.X_OK
                ):
                    return python_executable

                print(
                    f"Warning: No executable 'python' or 'python3' found in specified venv bin: {os.path.join(venv_folder, 'bin')}. Falling back to system Python.",
                    file=sys.stderr,
                )
            else:
                if venv_folder:
                    print(
                        f"Warning: Custom venv path '{venv_folder}' is invalid or not a directory. Falling back to system Python.",
                        file=sys.stderr,
                    )

        import shutil

        system_python3 = shutil.which("python3")
        if system_python3:
            return system_python3

        system_python = shutil.which("python")
        if system_python:
            return system_python

        print(
            "Warning: Neither 'python3' nor 'python' found in system PATH.",
            file=sys.stderr,
        )
        return "Warning: No Python found"

    def update_python_env_status(self, source_view=None):
        """Updates the status bar with the current Python environment info for the current tab."""
        python_interpreter = self.get_python_interpreter()
        python_version = "Unknown"
        status_text = python_interpreter

        if (
            "Warning:" in python_interpreter
            or "Not Found" in python_interpreter
            or "No active tab" in python_interpreter
        ):
            python_version = "N/A"
            status_text = f"Python Env: {python_interpreter}"
        else:
            try:
                result = subprocess.run(
                    [python_interpreter, "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                    encoding="utf-8",
                    errors="replace",
                )
                version_output = (result.stderr or result.stdout).strip()

                if result.returncode == 0 and "Python" in version_output:
                    parts = version_output.split()
                    if len(parts) > 1:
                        python_version = parts[1]
                    else:
                        python_version = version_output
                else:
                    print(
                        f"Warning: Failed to get version from '{python_interpreter}'. RC={result.returncode}, stderr: {result.stderr}, stdout: {result.stdout}",
                        file=sys.stderr,
                    )
                    python_version = "Version N/A"

            except FileNotFoundError:
                python_version = "Not Found"
                python_interpreter = os.path.basename(python_interpreter)
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

            display_path = python_interpreter
            if len(display_path) > 40:
                display_path = "..." + display_path[-37:]
            status_text = f"Env: {display_path} ({python_version})"

        if not self._status_timeout_id:
            self.status_label.set_text(status_text)
        else:
            pass

    def on_tab_switched(self, notebook, page, page_num):
        """Handler for tab switch event. Updates status bar."""
        print(f"Switched to tab index: {page_num}")
        if self._status_timeout_id:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None
        self.update_python_env_status()

    def on_page_removed(self, notebook, child, page_num):
        """Handler for page removed event. Updates status bar if the active tab changed."""
        print(f"Tab removed at index: {page_num}")
        current_page = notebook.get_current_page()
        if current_page != -1:
            self.update_python_env_status()
        else:
            self.status_label.set_text("No tabs open")

    def _set_status_message(
        self,
        text,
        temporary=False,
        temporary_source_view=None,
        timeout=STATUS_MESSAGE_TIMEOUT_MS,
    ):
        """Updates the status bar label, optionally resetting after a delay."""
        if self._status_timeout_id:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None

        self.status_label.set_text(text)

        if temporary:
            self._status_timeout_id = GLib.timeout_add(
                timeout,
                self._restore_default_status,
            )

    def _restore_default_status(self, *user_data):
        """Restores the status bar to the default (Python env info). Called by timeout or completion."""
        # Ensure we remove any existing timeout ID *before* potentially setting a new one in update_python_env_status
        if self._status_timeout_id:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None
        # Now update the status to the default environment info
        self.update_python_env_status()
        # The return GLib.SOURCE_REMOVE is only needed if called directly from GLib.timeout_add
        return GLib.SOURCE_REMOVE

    def on_remove_tab_clicked(self, *args):
        """Handles the Remove Tab action, triggered by hotkey (Ctrl+W)."""
        current_page_index = self.notebook.get_current_page()
        if current_page_index != -1:
            print(f"Removing tab at index: {current_page_index}")
            self.notebook.remove_page(current_page_index)

            self._set_status_message("Tab removed.", temporary=True)

            if self.notebook.get_n_pages() == 0:
                self.status_label.set_text("No tabs open. Press Ctrl+N for a new tab.")

        else:
            self._set_status_message("No tab selected to remove.", temporary=True)

    def on_pip_freeze_clicked(self, *args):
        """Handles the Pip Freeze action (Ctrl+Shift+P) for the current tab's environment."""
        tab_widgets = self._get_current_tab_widgets()
        if not tab_widgets:
            self._set_status_message("No active tab found.", temporary=True)
            return

        output_buffer = tab_widgets["output_buffer"]
        output_view = tab_widgets["output_view"]
        code_input = tab_widgets["code_input"]

        python_interpreter = self.get_python_interpreter()
        if "Warning:" in python_interpreter or "Not Found" in python_interpreter:
            self._set_status_message(
                f"Error: Cannot run pip freeze, invalid Python interpreter ({python_interpreter}). Check settings.",
                temporary=False,
            )
            output_buffer.set_text(
                f"Error: Invalid Python interpreter selected:\n{python_interpreter}\n\nPlease check the settings for this tab (Ctrl+T)."
            )
            return

        self._set_status_message(
            f"Running pip freeze with {os.path.basename(python_interpreter)}...",
            temporary=False,
            temporary_source_view=code_input,
        )
        output_buffer.set_text("Running pip freeze...\n")

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
            process = subprocess.Popen(
                [
                    python_interpreter,
                    "-m",
                    "pip",
                    "freeze",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            stdout_data, stderr_data = process.communicate(timeout=EXECUTION_TIMEOUT)

            if process.returncode == 0:
                output = stdout_data
                if not output.strip():
                    output = "# No packages installed in this environment."
                success = True
                if stderr_data:
                    error = f"--- Pip Warnings/Stderr ---\n{stderr_data}"
            else:
                if "No module named pip" in stderr_data:
                    error = f"Error: 'pip' module not found for interpreter '{python_interpreter}'.\nPlease ensure pip is installed in the selected environment."
                else:
                    error = f"Error running pip freeze (Exit Code: {process.returncode}):\n{stderr_data}"
                output = stdout_data

        except FileNotFoundError:
            error = f"Error: Python interpreter '{python_interpreter}' not found."
        except subprocess.TimeoutExpired:
            if process:
                process.kill()
                stdout_data, stderr_data = process.communicate()
                output = stdout_data
                error = f"--- Error: pip freeze timed out after {EXECUTION_TIMEOUT} seconds ---\n{stderr_data}"
            else:
                error = (
                    f"Error: pip freeze timed out after {EXECUTION_TIMEOUT} seconds."
                )
        except Exception as e:
            error = f"Error executing pip freeze: {e}"
            if process and process.poll() is None:
                process.kill()

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
    app = Gtk.Application.new(APP_ID, Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(application):
        windows = application.get_windows()
        if windows:
            print("Application already running. Presenting existing window.")
            windows[0].present()
        else:
            print("Application starting. Creating main window.")
            window = PythonRunnerApp()
            application.add_window(window)
            # window.show_all() # add_window should handle showing it

    app.connect("activate", do_activate)

    exit_status = app.run(sys.argv)
    print(f"Application exiting with status: {exit_status}")
    sys.exit(exit_status)


if __name__ == "__main__":
    GLib.set_prgname(APP_ID)
    main()
