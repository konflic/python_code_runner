#!/usr/bin/env python3

import os
import subprocess
import threading
import sys
import json
import shutil
import random
import string

import gi

try:
    from python_runner.version import VERSION
except ImportError:
    VERSION = "dev"

gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "3.0")

from gi.repository import Gtk, Gio, Pango, GtkSource, Gdk, GLib

APP_ID = "com.example.python-runner"
INITIAL_WIDTH, INITIAL_HEIGHT = 800, 600
DEFAULT_STYLE_SCHEME = "oblivion"
STATUS_MESSAGE_TIMEOUT_MS = 2000
DEFAULT_TAB_SIZE = 4
DEFAULT_TRANSLATE_TABS = True
DEFAULT_DRAW_WHITESPACES = False
DEFAULT_USE_CUSTOM_VENV = False
DEFAULT_VENV_FOLDER = ""
TAB_ID_LENGTH = 5

SETTING_DRAW_WHITESPACES = "draw_whitespaces"
SETTING_TAB_SIZE = "tab_size"
SETTING_TRANSLATE_TABS = "translate_tabs"
SETTING_COLOR_SCHEME_ID = "color_scheme_id"
SETTING_USE_CUSTOM_VENV = "use_custom_venv"
SETTING_VENV_FOLDER = "venv_folder"

CACHE_KEY_ID = "id"
CACHE_KEY_CODE = "code"
CACHE_KEY_SETTINGS = "settings"

CACHE_FILE_NAME = "python_runner_cache.json"
EXECUTION_TIMEOUT = 30

DEFAULT_TAB_SETTINGS = {
    SETTING_DRAW_WHITESPACES: DEFAULT_DRAW_WHITESPACES,
    SETTING_TAB_SIZE: DEFAULT_TAB_SIZE,
    SETTING_TRANSLATE_TABS: DEFAULT_TRANSLATE_TABS,
    SETTING_COLOR_SCHEME_ID: DEFAULT_STYLE_SCHEME,
    SETTING_USE_CUSTOM_VENV: DEFAULT_USE_CUSTOM_VENV,
    SETTING_VENV_FOLDER: DEFAULT_VENV_FOLDER,
}


class PythonRunnerApp(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title=f"Python Runner {VERSION}")

        self.set_default_size(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_size_request(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", self.on_destroy)
        self._status_timeout_id = None
        self._temporary_status_context = None

        self.cache_dir_path = self._get_app_cache_dir()
        self.cache_file_path = os.path.join(self.cache_dir_path, CACHE_FILE_NAME)

        self._setup_css()
        self._setup_ui()
        self._setup_hotkeys()

        cache_loaded = self._load_code_from_cache()

        if not cache_loaded:
            self._add_new_tab()
            self.on_show_hotkeys()

        self.update_python_env_status()

        self.show_all()

    def on_destroy(self, _):
        saved_cache = self._save_code_to_cache()
        if not saved_cache:
            print("ERROR: Failed to save code cache on exit!", file=sys.stderr)

        Gtk.main_quit()

    def _get_app_cache_dir(self):
        cache_dir = GLib.get_user_cache_dir()
        if not cache_dir:
            cache_dir = os.path.abspath(".")
            print(
                f"Warning: User cache directory not found. Using '{cache_dir}'.",
                file=sys.stderr,
            )
            app_cache_dir = cache_dir
        else:
            app_cache_dir = os.path.join(cache_dir, APP_ID)

        try:
            os.makedirs(app_cache_dir, exist_ok=True)
        except OSError as e:
            print(
                f"Error creating cache directory '{app_cache_dir}': {e}",
                file=sys.stderr,
            )
            fallback_dir = os.path.abspath(".")
            print(
                f"Falling back to using current directory: '{fallback_dir}'",
                file=sys.stderr,
            )
            return fallback_dir
        return app_cache_dir

    def _generate_unique_tab_id(self):
        existing_ids = set()
        for i in range(self.notebook.get_n_pages()):
            page_widget = self.notebook.get_nth_page(i)
            if page_widget and hasattr(page_widget, "tab_id"):
                existing_ids.add(page_widget.tab_id)

        chars = string.ascii_letters + string.digits
        while True:
            new_id = "".join(random.choices(chars, k=TAB_ID_LENGTH))
            if new_id not in existing_ids:
                return new_id

    def _save_code_to_cache(self):
        tabs_data = []
        n_pages = self.notebook.get_n_pages()

        for i in range(n_pages):
            page_widget = self.notebook.get_nth_page(i)

            if (
                page_widget
                and hasattr(page_widget, "tab_widgets")
                and hasattr(page_widget, "tab_settings")
                and hasattr(page_widget, "tab_id")
            ):
                tab_widgets = page_widget.tab_widgets
                tab_settings = page_widget.tab_settings
                tab_id = page_widget.tab_id

                if tab_settings.get(SETTING_USE_CUSTOM_VENV) and not tab_settings.get(
                    SETTING_VENV_FOLDER
                ):
                    tab_settings[SETTING_USE_CUSTOM_VENV] = False

                code_buffer = tab_widgets["code_buffer"]
                start_iter = code_buffer.get_start_iter()
                end_iter = code_buffer.get_end_iter()
                code = code_buffer.get_text(start_iter, end_iter, False)

                tabs_data.append(
                    {
                        CACHE_KEY_ID: tab_id,
                        CACHE_KEY_CODE: code,
                        CACHE_KEY_SETTINGS: tab_settings,
                    }
                )
            else:
                tab_label_text = "Unknown (Widget Error)"
                try:
                    tab_label_widget = self.notebook.get_tab_label(page_widget)
                    if isinstance(tab_label_widget, Gtk.Label):
                        tab_label_text = tab_label_widget.get_text()
                    elif isinstance(tab_label_widget, Gtk.EventBox) and isinstance(
                        tab_label_widget.get_child(), Gtk.Label
                    ):
                        tab_label_text = tab_label_widget.get_child().get_text()
                except Exception:
                    pass

                print(
                    f"Warning: Could not get widgets, settings, or ID for tab index {i} (label: '{tab_label_text}') during save.",
                    file=sys.stderr,
                )

        try:
            os.makedirs(self.cache_dir_path, exist_ok=True)
            temp_file_path = self.cache_file_path + ".tmp"
            with open(temp_file_path, "w", encoding="utf-8") as f:
                json.dump(tabs_data, f, indent=4)
            os.replace(temp_file_path, self.cache_file_path)

            return True
        except Exception as e:
            print(
                f"Error saving code cache to '{self.cache_file_path}': {e}",
                file=sys.stderr,
            )
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError as rm_e:
                    print(f"Error removing temp cache file: {rm_e}", file=sys.stderr)
            return False

    def _load_code_from_cache(self):
        if not os.path.exists(self.cache_file_path):
            return False

        try:
            with open(self.cache_file_path, "r", encoding="utf-8") as f:
                tabs_data = json.load(f)

            if not isinstance(tabs_data, list):
                print(
                    f"Error: Cache file format invalid (expected list): '{self.cache_file_path}'.",
                    file=sys.stderr,
                )
                return False

            if not tabs_data:
                return False

            while self.notebook.get_n_pages() > 0:
                self.notebook.remove_page(0)

            num_loaded = 0
            loaded_ids = set()
            for i, tab_data in enumerate(tabs_data):
                if not isinstance(tab_data, dict):
                    print(
                        f"Warning: Skipping invalid item at index {i} in cache.",
                        file=sys.stderr,
                    )
                    continue

                code = tab_data.get(CACHE_KEY_CODE, "")
                loaded_id = tab_data.get(CACHE_KEY_ID)
                loaded_settings = tab_data.get(CACHE_KEY_SETTINGS, {})

                final_tab_id = None
                if (
                    loaded_id
                    and isinstance(loaded_id, str)
                    and len(loaded_id) <= TAB_ID_LENGTH
                ):
                    if loaded_id not in loaded_ids:
                        final_tab_id = loaded_id
                        loaded_ids.add(loaded_id)
                    else:
                        print(
                            f"Warning: Duplicate ID '{loaded_id}' found in cache. Generating new ID.",
                            file=sys.stderr,
                        )
                elif loaded_id:
                    print(
                        f"Warning: Invalid ID '{loaded_id}' found in cache. Generating new ID.",
                        file=sys.stderr,
                    )

                final_settings = DEFAULT_TAB_SETTINGS.copy()
                if isinstance(loaded_settings, dict):
                    unknown_keys = []
                    for key, default_value in DEFAULT_TAB_SETTINGS.items():
                        if key in loaded_settings:
                            loaded_value = loaded_settings[key]
                            if isinstance(loaded_value, type(default_value)):
                                final_settings[key] = loaded_value
                            else:
                                pass
                    for loaded_key in loaded_settings:
                        if loaded_key not in DEFAULT_TAB_SETTINGS:
                            unknown_keys.append(loaded_key)
                    if unknown_keys:
                        print(
                            f"Warning: Ignoring unknown settings keys for loaded tab (ID: {final_tab_id or 'New'}): {', '.join(unknown_keys)}",
                            file=sys.stderr,
                        )

                    if (
                        final_settings.get(SETTING_USE_CUSTOM_VENV)
                        and not final_settings.get(SETTING_VENV_FOLDER, "").strip()
                    ):
                        final_settings[SETTING_USE_CUSTOM_VENV] = False
                else:
                    print(
                        f"Warning: Invalid 'settings' format for loaded tab (ID: {final_tab_id or 'New'}) in cache. Using defaults.",
                        file=sys.stderr,
                    )

                self._add_tab_with_content(
                    code, final_settings, existing_id=final_tab_id, save_cache=False
                )
                num_loaded += 1

            if self.notebook.get_n_pages() > 0:
                self.notebook.set_current_page(0)

            self.on_show_hotkeys()
            return True

        except json.JSONDecodeError as e:
            print(
                f"Error decoding cache file ({self.cache_file_path}): {e}. Load aborted.",
                file=sys.stderr,
            )
            while self.notebook.get_n_pages() > 0:
                self.notebook.remove_page(0)
            return False
        except Exception as e:
            print(
                f"Error loading from cache ({self.cache_file_path}): {e}. Load aborted.",
                file=sys.stderr,
            )
            while self.notebook.get_n_pages() > 0:
                self.notebook.remove_page(0)
            self._set_status_message("Error loading code from cache.")
            return False

    def _setup_css(self):
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
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(vbox)
        self.notebook = Gtk.Notebook(scrollable=True)
        vbox.pack_start(self.notebook, True, True, 0)
        self.notebook.connect("switch-page", self.on_tab_switched)
        self.notebook.connect("page-removed", self.on_page_removed)
        status_box = self._setup_statusbar()
        vbox.pack_start(status_box, False, False, 0)

    def _add_new_tab(self):
        initial_tab_settings = DEFAULT_TAB_SETTINGS.copy()
        self._add_tab_with_content(
            "import this", initial_tab_settings, existing_id=None, save_cache=True
        )

    def _add_tab_with_content(
        self, code, tab_settings, existing_id=None, save_cache=True
    ):
        tab_content_paned = self._create_tab_content(tab_settings)

        if (
            existing_id
            and isinstance(existing_id, str)
            and len(existing_id) <= TAB_ID_LENGTH
        ):
            tab_id = existing_id
        else:
            tab_id = self._generate_unique_tab_id()
        tab_content_paned.tab_id = tab_id

        code_buffer = tab_content_paned.tab_widgets["code_buffer"]
        code_buffer.set_text(code or "", -1)

        tab_label_widget = Gtk.Label(label=tab_id)

        self.notebook.append_page(tab_content_paned, tab_label_widget)
        self.notebook.show_all()
        new_page_index = self.notebook.get_n_pages() - 1
        self.notebook.set_current_page(new_page_index)

        self.update_python_env_status()

        if save_cache:
            self._save_code_to_cache()

    def _create_tab_content(self, initial_tab_settings):
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.tab_settings = initial_tab_settings.copy()
        paned.tab_widgets = {}

        code_buffer = GtkSource.Buffer()
        code_input = GtkSource.View.new_with_buffer(code_buffer)

        lang_manager = GtkSource.LanguageManager.get_default()
        python_lang = lang_manager.get_language("python3") or lang_manager.get_language(
            "python"
        )
        if python_lang:
            code_buffer.set_language(python_lang)
        else:
            print("Warning: Python syntax highlighting not available.", file=sys.stderr)

        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme_id = initial_tab_settings.get(
            SETTING_COLOR_SCHEME_ID, DEFAULT_STYLE_SCHEME
        )
        scheme = (
            style_manager.get_scheme(scheme_id)
            or style_manager.get_scheme(DEFAULT_STYLE_SCHEME)
            or style_manager.get_scheme("classic")
        )
        if scheme:
            code_buffer.set_style_scheme(scheme)
        else:
            print(f"Error: Could not find any valid color scheme.", file=sys.stderr)

        code_input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        code_input.set_monospace(True)
        code_input.set_show_line_numbers(True)
        code_input.set_highlight_current_line(True)
        code_input.set_auto_indent(True)
        code_input.set_indent_on_tab(True)
        code_input.set_tab_width(
            initial_tab_settings.get(SETTING_TAB_SIZE, DEFAULT_TAB_SIZE)
        )
        code_input.set_insert_spaces_instead_of_tabs(
            initial_tab_settings.get(SETTING_TRANSLATE_TABS, DEFAULT_TRANSLATE_TABS)
        )

        margin = 10
        code_input.set_left_margin(margin)
        code_input.set_right_margin(margin)
        code_input.set_top_margin(margin)
        code_input.set_bottom_margin(margin)

        space_drawer = code_input.get_space_drawer()
        space_drawer.set_enable_matrix(True)
        draw_ws = initial_tab_settings.get(
            SETTING_DRAW_WHITESPACES, DEFAULT_DRAW_WHITESPACES
        )
        types = (
            GtkSource.SpaceTypeFlags.SPACE | GtkSource.SpaceTypeFlags.TAB
            if draw_ws
            else GtkSource.SpaceTypeFlags.NONE
        )
        space_drawer.set_types_for_locations(GtkSource.SpaceLocationFlags.ALL, types)

        scrolled_code = Gtk.ScrolledWindow(
            hexpand=True, vexpand=True, shadow_type=Gtk.ShadowType.IN
        )
        scrolled_code.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_code.add(code_input)
        paned.add1(scrolled_code)

        output_buffer = Gtk.TextBuffer()
        output_view = Gtk.TextView(
            buffer=output_buffer,
            editable=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        output_view.set_left_margin(margin)
        output_view.set_right_margin(margin)
        output_view.set_top_margin(margin)
        output_view.set_bottom_margin(margin)

        scrolled_output = Gtk.ScrolledWindow(
            hexpand=True, vexpand=True, shadow_type=Gtk.ShadowType.IN
        )
        scrolled_output.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_output.add(output_view)
        paned.add2(scrolled_output)

        paned.set_position(INITIAL_HEIGHT // 2 - 30)
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
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_box.set_border_width(0)
        status_box.set_margin_start(6)
        status_box.set_margin_end(6)
        status_box.set_margin_top(0)
        status_box.set_margin_bottom(5)
        self.status_label = Gtk.Label(
            label="Ready", xalign=0.0, ellipsize=Pango.EllipsizeMode.END
        )
        status_box.pack_start(self.status_label, True, True, 0)
        return status_box

    def _setup_hotkeys(self):
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)
        keymap = {
            "<Control>R": self.on_run_clicked,
            "<Control>C": self.on_copy_clicked,
            "<Control>S": self.on_export_clicked,
            "<Control>T": self.on_settings_clicked,
            "<Control>comma": self.on_settings_clicked,
            "<Control>H": self.on_show_hotkeys,
            "<Control>N": self.on_new_tab_clicked,
            "<Control>W": self.on_remove_tab_clicked,
            "<Control>P": self.on_pip_freeze_clicked,
        }
        for accel, callback in keymap.items():
            key, mod = Gtk.accelerator_parse(accel)
            if key != 0:
                accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, callback)
            else:
                print(
                    f"Warning: Failed to parse accelerator '{accel}'", file=sys.stderr
                )

    def _get_current_tab_widgets_settings_id(self):
        idx = self.notebook.get_current_page()
        if idx == -1:
            return None, None, None
        paned = self.notebook.get_nth_page(idx)
        if (
            paned
            and hasattr(paned, "tab_widgets")
            and isinstance(paned.tab_widgets, dict)
            and hasattr(paned, "tab_settings")
            and isinstance(paned.tab_settings, dict)
            and hasattr(paned, "tab_id")
            and isinstance(paned.tab_id, str)
        ):
            return paned.tab_widgets, paned.tab_settings, paned.tab_id
        else:
            return None, None, None

    def _get_current_tab_widgets(self):
        widgets, _, _ = self._get_current_tab_widgets_settings_id()
        return widgets

    def _get_current_tab_id(self):
        _, _, tab_id = self._get_current_tab_widgets_settings_id()
        return tab_id

    def _run_code_thread(
        self, code, python_interpreter, output_buffer, output_view, source_view
    ):
        output, error, success = "", "", False
        process = None
        try:
            process = subprocess.Popen(
                [python_interpreter, "-u", "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout_data, stderr_data = process.communicate(timeout=EXECUTION_TIMEOUT)
            if process.returncode == 0:
                output = stdout_data
                success = True
                if stderr_data:
                    error = f"--- Warnings/Stderr Output ---\n{stderr_data}"
            else:
                output = stdout_data
                error = f"--- Error (Exit Code {process.returncode}) ---\n{stderr_data}"
        except FileNotFoundError:
            error = f"Error: Interpreter '{python_interpreter}' not found."
        except subprocess.TimeoutExpired:
            success = False
            if process:
                process.kill()
                try:
                    stdout_data, stderr_data = process.communicate(timeout=1)
                except Exception:
                    stdout_data, stderr_data = (
                        "",
                        "(Timeout/Error fetching output after kill)",
                    )
                output = stdout_data
                error = f"--- Error: Code timed out ({EXECUTION_TIMEOUT}s) ---\n{stderr_data}"
            else:
                error = f"Error: Code timed out ({EXECUTION_TIMEOUT}s)."
        except Exception as e:
            error = f"Error executing code: {e}"
            success = False
        finally:
            if process and process.poll() is None:
                try:
                    process.kill()
                    process.communicate(timeout=1)
                except Exception:
                    pass

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
        full_output = (output_text or "") + (
            ("\n" + error_text)
            if error_text and (output_text or "")
            else (error_text or "")
        )
        output_buffer.set_text(full_output)
        end_iter = output_buffer.get_end_iter()
        output_buffer.place_cursor(end_iter)
        output_view.scroll_to_mark(output_buffer.get_insert(), 0.0, False, 0.0, 1.0)

        current_widgets = self._get_current_tab_widgets()
        active_source_view = current_widgets["code_input"] if current_widgets else None
        if (
            source_view == active_source_view
            and source_view == self._temporary_status_context
        ):
            self._restore_default_status()

        return GLib.SOURCE_REMOVE

    def on_run_clicked(self, *args):
        tab_widgets, _, tab_id = self._get_current_tab_widgets_settings_id()
        if not tab_widgets:
            self._set_status_message("No active tab found.")
            return

        code_buffer = tab_widgets["code_buffer"]
        output_buffer = tab_widgets["output_buffer"]
        output_view = tab_widgets["output_view"]
        code_input = tab_widgets["code_input"]
        start_iter, end_iter = code_buffer.get_start_iter(), code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if not code.strip():
            self._set_status_message(
                f"Nothing to run.", temporary_source_view=code_input
            )
            return

        python_interpreter = self.get_python_interpreter()
        if python_interpreter.startswith("Warning:") or not os.path.exists(
            python_interpreter
        ):
            error_msg = f"Error: Invalid/missing Python ('{python_interpreter}'). Check settings (Ctrl+T)."
            self._set_status_message(error_msg)
            output_buffer.set_text(error_msg)
            return

        save_ok = self._save_code_to_cache()
        if not save_ok:
            self._set_status_message(
                "Failed to save cache before running!", temporary=False
            )

        output_buffer.set_text("")
        self._set_status_message(
            f"Running with {os.path.basename(python_interpreter)}...",
            temporary_source_view=code_input,
        )
        thread = threading.Thread(
            target=self._run_code_thread,
            args=(code, python_interpreter, output_buffer, output_view, code_input),
            daemon=True,
        )
        thread.start()

    def on_copy_clicked(self, *args):
        tab_widgets, _, tab_id = self._get_current_tab_widgets_settings_id()
        if not tab_widgets:
            return
        code_buffer, code_input = tab_widgets["code_buffer"], tab_widgets["code_input"]
        if code_buffer.get_has_selection():
            start, end = code_buffer.get_selection_bounds()
            text = code_buffer.get_text(start, end, True)
        else:
            start, end = code_buffer.get_start_iter(), code_buffer.get_end_iter()
            text = code_buffer.get_text(start, end, False)
        if text:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
            self._set_status_message(f"Code copied", temporary_source_view=code_input)
        else:
            self._set_status_message(
                f"Nothing to copy", temporary_source_view=code_input
            )

    def on_export_clicked(self, *args):
        tab_widgets, _, tab_id = self._get_current_tab_widgets_settings_id()
        if not tab_widgets or not tab_id:
            self._set_status_message("No active tab to export.")
            return
        code_buffer, code_input = tab_widgets["code_buffer"], tab_widgets["code_input"]
        start, end = code_buffer.get_start_iter(), code_buffer.get_end_iter()
        code = code_buffer.get_text(start, end, False)
        if not code.strip():
            self._set_status_message(
                f"No code to export", temporary_source_view=code_input
            )
            return

        dialog = Gtk.FileChooserDialog(
            title=f"Export Code From Tab {tab_id} As...",
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

        suggested = f"{tab_id}.py"
        dialog.set_current_name(suggested)

        py_filter = Gtk.FileFilter()
        py_filter.set_name("Python files (*.py)")
        py_filter.add_pattern("*.py")
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files (*.*)")
        all_filter.add_pattern("*")
        dialog.add_filter(py_filter)
        dialog.add_filter(all_filter)

        response = dialog.run()
        filename = None
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            if (
                filename
                and not os.path.splitext(filename)[1]
                and dialog.get_filter() == py_filter
            ):
                filename += ".py"
            elif (
                filename
                and dialog.get_filter() == py_filter
                and not filename.lower().endswith(".py")
            ):
                if not filename.lower().endswith(".py"):
                    filename += ".py"

            if filename:
                try:
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(code)
                    self._set_status_message(
                        f"Exported to {os.path.basename(filename)}",
                        temporary_source_view=code_input,
                    )
                except Exception as e:
                    print(f"Error saving file '{filename}': {e}", file=sys.stderr)
                    ed = Gtk.MessageDialog(
                        transient_for=self,
                        flags=0,
                        message_type=Gtk.MessageType.ERROR,
                        buttons=Gtk.ButtonsType.OK,
                        text="Error Exporting File",
                    )
                    ed.format_secondary_text(f"Could not save file:\n{e}")
                    ed.run()
                    ed.destroy()
                    self._set_status_message(
                        f"Error exporting file", temporary_source_view=code_input
                    )
            else:
                self._set_status_message(
                    f"Export failed (no filename)", temporary_source_view=code_input
                )
        elif response == Gtk.ResponseType.CANCEL:
            self._set_status_message(
                f"Export cancelled", temporary_source_view=code_input
            )
        dialog.destroy()

    def on_settings_clicked(self, *args):
        current_tab_index = self.notebook.get_current_page()
        if current_tab_index == -1:
            self._set_status_message("No active tab to configure.")
            return

        current_paned = self.notebook.get_nth_page(current_tab_index)
        current_tab_id = getattr(current_paned, "tab_id", None)

        if (
            not current_paned
            or not hasattr(current_paned, "tab_settings")
            or not current_tab_id
        ):
            print(
                f"Warning: Settings or ID missing for tab index {current_tab_index}.",
                file=sys.stderr,
            )
            self._set_status_message("Error accessing tab settings/ID.")
            return

        current_tab_settings = current_paned.tab_settings.copy()

        dialog = Gtk.Dialog(
            title=f"Settings for Tab {current_tab_id}",
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
        editor_frame = Gtk.Frame(label="Editor Settings")
        main_vbox.pack_start(editor_frame, False, False, 0)
        editor_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        editor_frame.add(editor_vbox)

        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme_ids = style_manager.get_scheme_ids() or []
        schemes_data = []
        if scheme_ids:
            for sid in sorted(scheme_ids):
                scheme = style_manager.get_scheme(sid)
                schemes_data.append({"id": sid, "name": scheme.get_name() or sid})
            schemes_data.sort(key=lambda x: x["name"].lower())

        dw_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        editor_vbox.pack_start(dw_hbox, False, False, 0)
        dw_label = Gtk.Label(label="Draw Whitespaces:", xalign=0.0)
        dw_hbox.pack_start(dw_label, True, True, 0)
        dw_switch = Gtk.Switch(
            active=current_tab_settings.get(
                SETTING_DRAW_WHITESPACES, DEFAULT_DRAW_WHITESPACES
            )
        )
        dw_hbox.pack_end(dw_switch, False, False, 0)

        cs_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        editor_vbox.pack_start(cs_hbox, False, False, 0)
        cs_label = Gtk.Label(label="Color Scheme:", xalign=0.0)
        cs_hbox.pack_start(cs_label, False, False, 0)
        cs_combo = Gtk.ComboBoxText()
        cs_hbox.pack_start(cs_combo, True, True, 0)
        cs_combo.set_size_request(150, -1)
        active_idx = -1
        current_cs_id = current_tab_settings.get(
            SETTING_COLOR_SCHEME_ID, DEFAULT_STYLE_SCHEME
        )
        for i, si in enumerate(schemes_data):
            cs_combo.append(si["id"], si["name"])
        active_idx = next(
            (i for i, si in enumerate(schemes_data) if si["id"] == current_cs_id), -1
        )
        if active_idx != -1:
            cs_combo.set_active(active_idx)
        elif schemes_data:
            cs_combo.set_active(0)
            print(
                f"Warn: Scheme '{current_cs_id}' not found for tab {current_tab_id}. Selecting first.",
                file=sys.stderr,
            )
        elif not schemes_data:
            cs_label.set_sensitive(False)
            cs_combo.set_sensitive(False)

        ts_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        editor_vbox.pack_start(ts_hbox, False, False, 0)
        ts_label = Gtk.Label(label="Tab Size (Spaces):", xalign=0.0)
        ts_hbox.pack_start(ts_label, True, True, 0)
        ts_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
        ts_spin.set_value(current_tab_settings.get(SETTING_TAB_SIZE, DEFAULT_TAB_SIZE))
        ts_hbox.pack_end(ts_spin, False, False, 0)

        tt_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        editor_vbox.pack_start(tt_hbox, False, False, 0)
        tt_label = Gtk.Label(label="Use Spaces Instead of Tabs:", xalign=0.0)
        tt_hbox.pack_start(tt_label, True, True, 0)
        tt_switch = Gtk.Switch(
            active=current_tab_settings.get(
                SETTING_TRANSLATE_TABS, DEFAULT_TRANSLATE_TABS
            )
        )
        tt_hbox.pack_end(tt_switch, False, False, 0)

        venv_frame = Gtk.Frame(label="Python Environment")
        main_vbox.pack_start(venv_frame, False, False, 0)
        venv_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        venv_frame.add(venv_vbox)
        use_custom = current_tab_settings.get(
            SETTING_USE_CUSTOM_VENV, DEFAULT_USE_CUSTOM_VENV
        )
        venv_folder = current_tab_settings.get(SETTING_VENV_FOLDER, DEFAULT_VENV_FOLDER)

        cv_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        venv_vbox.pack_start(cv_hbox, False, False, 0)
        cv_label = Gtk.Label(label="Use Custom Virtual Environment:", xalign=0.0)
        cv_hbox.pack_start(cv_label, True, True, 0)
        cv_switch = Gtk.Switch(active=use_custom)
        cv_hbox.pack_end(cv_switch, False, False, 0)

        vp_outer_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        venv_vbox.pack_start(vp_outer_hbox, False, False, 0)
        vp_label = Gtk.Label(label="Venv Path:", xalign=0.0)
        vp_outer_hbox.pack_start(vp_label, False, False, 0)
        vp_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vp_outer_hbox.pack_start(vp_controls, True, True, 0)
        vp_entry = Gtk.Entry(
            text=venv_folder,
            sensitive=cv_switch.get_active(),
            xalign=0.0,
            placeholder_text="Path to venv directory",
        )
        vp_controls.pack_start(vp_entry, True, True, 0)
        vp_button = Gtk.Button(label="Browse...", sensitive=cv_switch.get_active())
        vp_controls.pack_start(vp_button, False, False, 0)

        def _toggle_venv(switch, *args):
            active = switch.get_active()
            vp_entry.set_sensitive(active)
            vp_button.set_sensitive(active)

        cv_switch.connect("notify::active", _toggle_venv)

        def _browse(button):
            fd = Gtk.FileChooserDialog(
                title="Select Venv Folder",
                parent=dialog,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            fd.add_buttons(
                Gtk.STOCK_CANCEL,
                Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OPEN,
                Gtk.ResponseType.OK,
            )
            cp = vp_entry.get_text()
            if cp and os.path.isdir(cp):
                try:
                    fd.set_current_folder(cp)
                except GLib.Error as e:
                    print(f"Warn: Cannot set folder path '{cp}': {e}", file=sys.stderr)
            elif os.path.isdir(os.path.expanduser("~")):
                fd.set_current_folder(os.path.expanduser("~"))
            resp = fd.run()
            if resp == Gtk.ResponseType.OK:
                folder = fd.get_filename()
                vp_entry.set_text(folder or "")
            fd.destroy()

        vp_button.connect("clicked", _browse)

        def _apply_changes():
            apply_idx = self.notebook.get_current_page()
            if apply_idx == -1:
                print(
                    "Error: No active tab found when applying settings.",
                    file=sys.stderr,
                )
                self._set_status_message("Error applying settings (no active tab).")
                return False

            target_paned = self.notebook.get_nth_page(apply_idx)
            target_id = getattr(target_paned, "tab_id", None)

            if (
                not target_paned
                or not hasattr(target_paned, "tab_settings")
                or not target_id
            ):
                print(
                    f"Error: Cannot find target tab or its data (index {apply_idx}) to apply settings.",
                    file=sys.stderr,
                )
                self._set_status_message("Error applying settings.")
                return False

            changed = False
            target_settings = target_paned.tab_settings

            new_cs_id = cs_combo.get_active_id()
            new_draw_ws = dw_switch.get_active()
            new_tab_size = ts_spin.get_value_as_int()
            new_translate_tabs = tt_switch.get_active()
            new_use_custom = cv_switch.get_active()
            new_venv_path = vp_entry.get_text().strip()

            if new_use_custom and not new_venv_path:
                new_use_custom = False
                cv_switch.set_active(False)
                new_venv_path = ""
                vp_entry.set_text("")
            elif not new_use_custom:
                new_venv_path = ""
                if vp_entry.get_text():
                    vp_entry.set_text("")

            if (
                cs_combo.get_sensitive()
                and new_cs_id
                and target_settings.get(SETTING_COLOR_SCHEME_ID) != new_cs_id
            ):
                target_settings[SETTING_COLOR_SCHEME_ID] = new_cs_id
                changed = True
            if (
                target_settings.get(SETTING_DRAW_WHITESPACES, DEFAULT_DRAW_WHITESPACES)
                != new_draw_ws
            ):
                target_settings[SETTING_DRAW_WHITESPACES] = new_draw_ws
                changed = True
            if target_settings.get(SETTING_TAB_SIZE, DEFAULT_TAB_SIZE) != new_tab_size:
                target_settings[SETTING_TAB_SIZE] = new_tab_size
                changed = True
            if (
                target_settings.get(SETTING_TRANSLATE_TABS, DEFAULT_TRANSLATE_TABS)
                != new_translate_tabs
            ):
                target_settings[SETTING_TRANSLATE_TABS] = new_translate_tabs
                changed = True
            if (
                target_settings.get(SETTING_USE_CUSTOM_VENV, DEFAULT_USE_CUSTOM_VENV)
                != new_use_custom
            ):
                target_settings[SETTING_USE_CUSTOM_VENV] = new_use_custom
                changed = True
            if (
                target_settings.get(SETTING_VENV_FOLDER, DEFAULT_VENV_FOLDER)
                != new_venv_path
            ):
                target_settings[SETTING_VENV_FOLDER] = new_venv_path
                changed = True

            if changed:
                self.apply_tab_settings(apply_idx)
                self.update_python_env_status()
                saved = self._save_code_to_cache()
                if saved:
                    self._set_status_message(f"Settings applied.")
                else:
                    self._set_status_message(
                        f"Settings applied, but FAILED TO SAVE cache!",
                        temporary=False,
                    )
                return True
            else:
                self._set_status_message(f"Settings unchanged.")
                return False

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
                self._set_status_message(f"Settings cancelled.")
                break
        dialog.destroy()

    def on_new_tab_clicked(self, *args):
        self._add_new_tab()
        self.on_show_hotkeys()

    def on_show_hotkeys(self, *args):
        tab_widgets, _, _ = self._get_current_tab_widgets_settings_id()
        if not tab_widgets:
            self._set_status_message("No active tab to show hotkeys in.")
            return
        output_buffer, output_view = (
            tab_widgets["output_buffer"],
            tab_widgets["output_view"],
        )
        hotkey_list = """--- Hotkeys ---
Ctrl+R         : Run Code
Ctrl+C         : Copy Code/Selection
Ctrl+S         : Export Code to File...
Ctrl+T / Ctrl+,: Open Tab Settings
Ctrl+H         : Show Hotkeys (this list)
Ctrl+N         : New Tab
Ctrl+W         : Remove Current Tab
Ctrl+P         : Pip Freeze (list packages)
"""
        if output_buffer and output_view:
            output_buffer.set_text(hotkey_list)
            start = output_buffer.get_start_iter()
            output_view.scroll_to_iter(start, 0.0, False, 0.0, 0.0)
        else:
            print("Warn: Cannot display hotkeys.", file=sys.stderr)
            self._set_status_message(f"Error displaying hotkeys.")

    def apply_tab_settings(self, page_index):
        paned = self.notebook.get_nth_page(page_index)

        if (
            not paned
            or not hasattr(paned, "tab_widgets")
            or not hasattr(paned, "tab_settings")
            or not isinstance(paned.tab_widgets, dict)
            or not isinstance(paned.tab_settings, dict)
        ):
            return

        widgets, settings = paned.tab_widgets, paned.tab_settings
        inp, buf, draw = (
            widgets.get("code_input"),
            widgets.get("code_buffer"),
            widgets.get("space_drawer"),
        )
        if not inp or not buf or not draw:
            pass

        if buf:
            sm = GtkSource.StyleSchemeManager.get_default()
            sid = settings.get(SETTING_COLOR_SCHEME_ID, DEFAULT_STYLE_SCHEME)
            s = (
                sm.get_scheme(sid)
                or sm.get_scheme(DEFAULT_STYLE_SCHEME)
                or sm.get_scheme("classic")
            )
            if s:
                cur = buf.get_style_scheme()
                if not cur or cur.get_id() != s.get_id():
                    buf.set_style_scheme(s)

        if inp and draw:
            draw_ws = settings.get(SETTING_DRAW_WHITESPACES, DEFAULT_DRAW_WHITESPACES)
            types = (
                GtkSource.SpaceTypeFlags.SPACE | GtkSource.SpaceTypeFlags.TAB
                if draw_ws
                else GtkSource.SpaceTypeFlags.NONE
            )
            draw.set_types_for_locations(GtkSource.SpaceLocationFlags.ALL, types)

        if inp:
            size = settings.get(SETTING_TAB_SIZE, DEFAULT_TAB_SIZE)
            trans = settings.get(SETTING_TRANSLATE_TABS, DEFAULT_TRANSLATE_TABS)
            if inp.get_tab_width() != size:
                inp.set_tab_width(size)
            if inp.get_insert_spaces_instead_of_tabs() != trans:
                inp.set_insert_spaces_instead_of_tabs(trans)
            inp.queue_draw()

    def get_python_interpreter(self):
        idx = self.notebook.get_current_page()

        if idx == -1:
            return "Warning: No active tab"

        paned = self.notebook.get_nth_page(idx)
        tab_id = getattr(paned, "tab_id", f"Index {idx}")

        if not paned or not hasattr(paned, "tab_settings"):
            settings = DEFAULT_TAB_SETTINGS
        else:
            settings = paned.tab_settings

        use_custom = settings.get(SETTING_USE_CUSTOM_VENV, DEFAULT_USE_CUSTOM_VENV)
        venv_folder = settings.get(SETTING_VENV_FOLDER, DEFAULT_VENV_FOLDER)

        # Set current working directory venv
        if venv_folder == DEFAULT_VENV_FOLDER:
            os.chdir(os.path.expanduser("~"))
        else:
            new_cwd = venv_folder.split("venv")[0]
            os.chdir(new_cwd)

        if use_custom and not venv_folder.strip():
            use_custom = False

        if use_custom:
            if venv_folder and os.path.isdir(venv_folder):
                found = None
                for bindir in ["bin", "Scripts"]:
                    binpath = os.path.join(venv_folder, bindir)
                    if os.path.isdir(binpath):
                        for name in ["python3", "python", "python.exe"]:
                            exe = os.path.join(binpath, name)
                            if os.path.isfile(exe) and os.access(exe, os.X_OK):
                                found = exe
                                return found
            elif venv_folder:
                print(
                    f"Warn: Custom venv path '{venv_folder}' for tab {tab_id} is not a valid directory. Falling back.",
                    file=sys.stderr,
                )

        system_py = shutil.which("python3") or shutil.which("python")
        if system_py:
            return system_py

        print(
            f"Error: No 'python3' or 'python' found in PATH (needed for tab {tab_id}).",
            file=sys.stderr,
        )
        return "Warning: No Python found"

    def update_python_env_status(self, source_view=None):
        py_interp = self.get_python_interpreter()

        status_text = "Ready"

        py_ver, status_suffix = "Unknown", py_interp
        if py_interp.startswith("Warning:"):
            status_suffix = f"Python Env: {py_interp}"
        elif not os.path.exists(py_interp):
            status_suffix = f"Python Env: Not Found ('{os.path.basename(py_interp)}')"
        else:
            try:
                res = subprocess.run(
                    [py_interp, "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                    encoding="utf-8",
                    errors="replace",
                )
                version_output = (res.stderr or res.stdout or "").strip()
                if res.returncode == 0 and "Python" in version_output:
                    parts = version_output.split()
                    py_ver = parts[1] if len(parts) > 1 else version_output
                else:
                    if res.returncode != 0 and not (
                        "No such file" in (res.stderr or "")
                        or "not found" in (res.stderr or "")
                    ):
                        pass
                    py_ver = "Version N/A"
            except FileNotFoundError:
                py_ver = "Not Found"
                py_interp = os.path.basename(py_interp)
            except subprocess.TimeoutExpired:
                py_ver = "Timeout"
            except Exception as e:
                print(
                    f"Error checking Python version for '{py_interp}': {e}",
                    file=sys.stderr,
                )
                py_ver = "Error"

            status_suffix = f"{py_interp} ({py_ver})"
            status_text = status_suffix

        if not self._status_timeout_id:
            self.status_label.set_text(status_text)

    def on_tab_switched(self, notebook, page, page_num):
        if self._status_timeout_id:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None
            self._temporary_status_context = None

        self.update_python_env_status()

    def on_page_removed(self, notebook, child, page_num):
        current_page = notebook.get_current_page()
        if current_page != -1:
            self.update_python_env_status()
        else:
            if self._status_timeout_id:
                GLib.source_remove(self._status_timeout_id)
                self._status_timeout_id = None
                self._temporary_status_context = None
        self.status_label.set_text("No tabs open. Press Ctrl+N for a new tab.")

    def _set_status_message(
        self,
        text,
        temporary=True,
        temporary_source_view=None,
        timeout=STATUS_MESSAGE_TIMEOUT_MS,
    ):
        if self._status_timeout_id:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None

        self.status_label.set_text(text)

        if temporary:
            self._temporary_status_context = temporary_source_view
            self._status_timeout_id = GLib.timeout_add(
                timeout, self._restore_default_status
            )
        else:
            self._temporary_status_context = None

    def _restore_default_status(self, *user_data):
        self._status_timeout_id = None
        self.update_python_env_status()
        self._temporary_status_context = None
        return GLib.SOURCE_REMOVE

    def on_remove_tab_clicked(self, *args):
        idx = self.notebook.get_current_page()
        if idx != -1:
            page = self.notebook.get_nth_page(idx)
            num_pages = self.notebook.get_n_pages()

            if num_pages <= 1:
                self._set_status_message("Cannot remove the last tab.", temporary=True)
                return

            tab_id = getattr(page, "tab_id", f"Index {idx}")
            self.notebook.remove_page(idx)
            self._save_code_to_cache()

            new_widgets = self._get_current_tab_widgets()
            new_input_view = new_widgets["code_input"] if new_widgets else None
            self._set_status_message(
                f"Tab '{tab_id}' removed.", temporary_source_view=new_input_view
            )
        else:
            self._set_status_message("No tab selected to remove.")

    def on_pip_freeze_clicked(self, *args):
        widgets, _, tab_id = self._get_current_tab_widgets_settings_id()
        if not widgets:
            self._set_status_message("No active tab found.")
            return
        out_buf, out_view, inp = (
            widgets["output_buffer"],
            widgets["output_view"],
            widgets["code_input"],
        )
        py_interp = self.get_python_interpreter()
        if py_interp.startswith("Warning:") or not os.path.exists(py_interp):
            msg = (
                f"Error: Cannot run pip freeze, invalid/missing Python ('{py_interp}')."
            )
            self._set_status_message(msg, temporary=False)
            out_buf.set_text(f"{msg}\nPlease check settings (Ctrl+T).")
            return

        self._set_status_message(
            f"Running pip freeze ({os.path.basename(py_interp)})...",
            temporary_source_view=inp,
        )
        out_buf.set_text("Running pip freeze...\n")
        threading.Thread(
            target=self._run_pip_freeze_thread,
            args=(py_interp, out_buf, out_view, inp),
            daemon=True,
        ).start()

    def _run_pip_freeze_thread(
        self, python_interpreter, output_buffer, output_view, source_view
    ):
        output, error, success = "", "", False
        process = None
        try:
            cmd = [python_interpreter, "-m", "pip", "freeze"]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout, stderr = process.communicate(timeout=EXECUTION_TIMEOUT)
            if process.returncode == 0:
                output = stdout or "# No packages installed."
                success = True
                if stderr:
                    error = f"--- Pip Warnings/Stderr ---\n{stderr}"
            else:
                if "No module named pip" in stderr:
                    error = f"Error: 'pip' module not found for '{os.path.basename(python_interpreter)}'."
                else:
                    error = f"Error running pip freeze (RC: {process.returncode}):\n{stderr}"
                output = stdout
        except FileNotFoundError:
            error = f"Error: Interpreter '{python_interpreter}' not found."
        except subprocess.TimeoutExpired:
            success = False
            if process:
                process.kill()
                try:
                    stdout, stderr = process.communicate(timeout=1)
                except Exception:
                    stdout, stderr = "", "(Timeout/Error fetching output after kill)"
                output = stdout
                error = f"--- Error: pip freeze timed out ({EXECUTION_TIMEOUT}s) ---\n{stderr}"
            else:
                error = f"Error: pip freeze timed out ({EXECUTION_TIMEOUT}s)."
        except Exception as e:
            error = f"Error executing pip freeze: {e}"
            success = False
        finally:
            if process and process.poll() is None:
                try:
                    process.kill()
                    process.communicate(timeout=1)
                except Exception:
                    pass
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
    GLib.set_prgname(APP_ID)
    app = Gtk.Application.new(APP_ID, Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(application):
        if application.get_windows():
            application.get_windows()[0].present()
        else:
            window = PythonRunnerApp()
            application.add_window(window)

    app.connect("activate", do_activate)
    exit_status = app.run(sys.argv)
    sys.exit(exit_status)


if __name__ == "__main__":
    main()
