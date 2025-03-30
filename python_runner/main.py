#!/usr/bin/env python3

import os
import subprocess
import threading
import sys

import gi

from python_runner.version import VERSION

gi.require_version("Gtk", "3.0")
gi.require_version("GtkSource", "3.0")

from gi.repository import Gtk, Gio, Pango, GtkSource, Gdk, GLib

# --- Constants ---
APP_ID = "com.example.python-runner"  # Example App ID
SETTINGS_SCHEMA = APP_ID  # Schema ID should match the App ID or be specific
INITIAL_WIDTH, INITIAL_HEIGHT = 700, 500
DEFAULT_STYLE_SCHEME = (
    "solarized-dark"  # Or another available theme like "classic", "tango"
)
STATUS_MESSAGE_TIMEOUT_MS = 3000  # 3 seconds

# Settings Keys
SETTING_DRAW_WHITESPACES = "draw-whitespaces"
SETTING_TAB_SIZE = "tab-size"
SETTING_TRANSLATE_TABS = "translate-tabs"
SETTING_USE_CUSTOM_VENV = "use-custom-venv"
SETTING_VENV_FOLDER = "venv-folder"
SETTING_COLOR_SCHEME_ID = "color-scheme-id"
CACHE_FILE_NAME = "python_runner_code_cache.txt"
EXECUTION_TIMEOUT = 30
# --- End Constants ---


class PythonRunnerApp(Gtk.Window):
    """
    A simple GTK application to write and run Python code snippets, now without a toolbar.
    All functionality is accessed via hotkeys.
    """

    def __init__(self):
        Gtk.Window.__init__(self, title=f"Python Runner {VERSION}")
        self.set_default_size(INITIAL_WIDTH, INITIAL_HEIGHT)
        self.set_size_request(INITIAL_WIDTH, INITIAL_HEIGHT)  # Minimum size
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", self.on_destroy)
        self.connect("destroy", Gtk.main_quit)
        self._status_timeout_id = None

        self._setup_settings()
        self._setup_css()  # CSS needs to be setup before UI elements that use names/classes
        self._setup_ui()
        self._setup_hotkeys()  # Setup hotkeys after UI

        self.cache_file_path = self._get_cache_file_path()
        self._load_code_from_cache()  # Load from cache

        self.apply_settings()  # Apply initial settings from schema
        self.update_python_env_status()  # Initial status update
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
        """Saves the current code to the cache file."""
        code_buffer = self.code_input.get_buffer()
        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        try:
            # Ensure the cache directory exists
            cache_dir = os.path.dirname(self.cache_file_path)
            os.makedirs(cache_dir, exist_ok=True)
            with open(self.cache_file_path, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"Code saved to cahce {self.cache_file_path}")
            return True  # Keep the timer running
        except Exception as e:
            print(f"Error saving to cache: {e}", file=sys.stderr)
            return True  # Keep the timer running - try again

    def _load_code_from_cache(self):
        """Loads code from the cache file, if it exists."""
        try:
            if os.path.exists(self.cache_file_path):
                with open(self.cache_file_path, "r", encoding="utf-8") as f:
                    code = f.read()
                self.code_buffer.set_text(code, -1)
                self._set_status_message("Code loaded from cache.", temporary=True)
        except Exception as e:
            print(f"Error loading from cache: {e}", file=sys.stderr)
            self._set_status_message("Error loading code from cache.", temporary=True)

    def _setup_settings(self):
        """Initializes GSettings."""
        # Ensure the schema file is installed or specified via GSETTINGS_SCHEMA_DIR
        schema_source = Gio.SettingsSchemaSource.get_default()
        if schema_source:
            schema = schema_source.lookup(SETTINGS_SCHEMA, True)  # Recursive lookup
            if schema:
                print(f"GSettings schema '{SETTINGS_SCHEMA}' found.")
                self.settings = Gio.Settings.new(SETTINGS_SCHEMA)
                self.settings.connect("changed", self.on_settings_changed)
            else:
                print(
                    f"Warning: GSettings schema '{SETTINGS_SCHEMA}' not found. Using defaults.",
                    file=sys.stderr,
                )
                # Use a dummy settings object if schema is not found
                self.settings = Gio.Settings.new_with_path(
                    SETTINGS_SCHEMA, "/dev/null/"
                )  # Non-persistent dummy
        else:
            print(
                "Warning: Default GSettings schema source not found. Using defaults.",
                file=sys.stderr,
            )
            self.settings = Gio.Settings.new_with_path(
                SETTINGS_SCHEMA, "/dev/null/"
            )  # Non-persistent dummy

    def _setup_css(self):
        """Loads and applies CSS styles."""
        css_provider = Gtk.CssProvider()
        # --- CSS (removed toolbar styles) ---
        css = f"""
        /* Style for the current line highlight (built-in) */
        textview text selection:focus, textview text selection {{
            background-color: alpha(#333333, 0.5); /* Example semi-transparent highlight */
        }}
        """
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _setup_ui(self):
        """Builds the main UI structure (toolbar removed)."""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(vbox)

        # Toolbar is removed from here
        # toolbar = self._setup_toolbar()
        # vbox.pack_start(toolbar, False, False, 0)
        # toolbar.set_visible(False)

        self.paned = self._setup_panes_and_views()
        vbox.pack_start(self.paned, True, True, 0)

        status_box = self._setup_statusbar()
        vbox.pack_start(status_box, False, False, 0)

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

    def _setup_panes_and_views(self):
        """Creates the Paned widget and the code/output views."""
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)

        # --- Code Input Area ---
        self.code_input = GtkSource.View()
        self.code_buffer = GtkSource.Buffer()
        self.code_input.set_buffer(self.code_buffer)

        # Syntax Highlighting
        lang_manager = GtkSource.LanguageManager.get_default()
        python_lang = lang_manager.get_language("python3")  # Use python3 if available
        if not python_lang:
            python_lang = lang_manager.get_language("python")
        if python_lang:
            self.code_buffer.set_language(python_lang)
        else:
            print("Warning: Python syntax highlighting not available.", file=sys.stderr)

        # Editor Features (rest of the method remains the same)
        self.code_input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.code_input.set_monospace(True)
        self.code_input.set_show_line_numbers(True)
        self.code_input.set_highlight_current_line(True)
        self.code_input.set_auto_indent(True)
        self.code_input.set_indent_on_tab(True)

        # Margins (Padding)
        margin = 10
        self.code_input.set_left_margin(margin)
        self.code_input.set_right_margin(margin)
        self.code_input.set_top_margin(margin)
        self.code_input.set_bottom_margin(margin)

        # Space Drawer (Managed by apply_settings)
        self.space_drawer = self.code_input.get_space_drawer()
        self.space_drawer.set_enable_matrix(True)

        scrolled_code = Gtk.ScrolledWindow()
        scrolled_code.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_code.set_hexpand(True)
        scrolled_code.set_vexpand(True)
        scrolled_code.add(self.code_input)
        paned.add1(scrolled_code)  # Add code area to the top pane

        # --- Output Area ---
        self.output_buffer = Gtk.TextBuffer()
        self.output_view = Gtk.TextView(buffer=self.output_buffer)
        self.output_view.set_editable(False)
        self.output_view.set_monospace(True)
        self.output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        # Margins (Padding)
        self.output_view.set_left_margin(margin)
        self.output_view.set_right_margin(margin)
        self.output_view.set_top_margin(margin)
        self.output_view.set_bottom_margin(margin)

        scrolled_output = Gtk.ScrolledWindow()
        scrolled_output.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_output.set_hexpand(True)
        scrolled_output.set_vexpand(True)
        scrolled_output.add(self.output_view)
        paned.add2(scrolled_output)  # Add output area to the bottom pane

        # Set initial pane position (roughly half)
        paned.set_position(INITIAL_HEIGHT // 2)

        return paned

    def _setup_statusbar(self):
        """Creates the status bar area."""
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_box.set_border_width(0)  # Use margin instead if needed
        status_box.set_margin_start(6)
        status_box.set_margin_end(6)
        status_box.set_margin_top(0)
        status_box.set_margin_bottom(5)

        self.status_label = Gtk.Label(label="Ready", xalign=0.0)  # Align left
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        status_box.pack_start(self.status_label, True, True, 0)  # Expand label

        return status_box

    # --- Event Handlers ---
    def _run_code_thread(self, code, python_interpreter):
        """Worker thread function to execute Python code."""
        output = ""
        error = ""
        success = False
        try:
            # Execute the code using the selected Python interpreter
            result = subprocess.run(
                [python_interpreter, "-c", code],
                capture_output=True,
                text=True,
                check=False,  # Don't raise exception on non-zero exit code
                timeout=EXECUTION_TIMEOUT,  # Add a timeout (e.g., 30 seconds)
            )
            if result.returncode == 0:
                output = result.stdout
                success = True
            else:
                # Combine stdout and stderr on error for context
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

        # Schedule UI update back on the main GTK thread
        GLib.idle_add(self._update_output_view, output, error, success)

    def _update_output_view(self, output_text, error_text, success):
        """Updates the output view and status (runs in main thread)."""
        full_output = output_text
        if error_text:
            if full_output:
                full_output += "\n" + error_text
            else:
                full_output = error_text

        self.output_buffer.set_text(full_output)
        # Scroll to the end of the output
        end_iter = self.output_buffer.get_end_iter()
        self.output_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 0.0)

        status_msg = "Execution finished" if success else "Execution failed"
        self._set_status_message(status_msg, temporary=True)

        return GLib.SOURCE_REMOVE  # Indicate the idle task is done

    def on_run_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the Run action, triggered by hotkey (Ctrl+R)."""
        code_buffer = self.code_input.get_buffer()
        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if not code.strip():
            self._set_status_message("Nothing to run.", temporary=True)
            return

        python_interpreter = self.get_python_interpreter()

        # Set status and clear output
        self._set_status_message(
            f"Running with {os.path.basename(python_interpreter)}..."
        )
        self.output_buffer.set_text("")  # Clear previous output

        # Start execution in a separate thread
        thread = threading.Thread(
            target=self._run_code_thread, args=(code, python_interpreter)
        )
        thread.daemon = True  # Allow app to exit even if thread is running
        thread.start()

    def on_copy_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the Copy action, triggered by hotkey (Ctrl+C)."""
        code_buffer = self.code_input.get_buffer()
        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if code:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(code, -1)
            self._set_status_message("Code copied to clipboard", temporary=True)
        else:
            self._set_status_message("Nothing to copy", temporary=True)

    def on_export_clicked(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Handles the Export action, triggered by hotkey (Ctrl+S)."""
        code_buffer = self.code_input.get_buffer()
        start_iter = code_buffer.get_start_iter()
        end_iter = code_buffer.get_end_iter()
        code = code_buffer.get_text(start_iter, end_iter, False)

        if not code.strip():
            self._set_status_message("No code to save", temporary=True)
            return

        dialog = Gtk.FileChooserDialog(
            title="Export Code As...",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )

        dialog.add_buttons(
            "_Cancel",
            Gtk.ResponseType.CANCEL,  # Use underscore for mnemonic
            "_Save",
            Gtk.ResponseType.OK,
        )

        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name("script.py")  # More generic default

        # Add Python file filter
        py_filter = Gtk.FileFilter()
        py_filter.set_name("Python files (*.py)")
        py_filter.add_pattern("*.py")
        dialog.add_filter(py_filter)

        # Add all files filter
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files (*.*)")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)

        response = dialog.run()
        filename = None

        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            # Ensure .py extension if not present and Python filter is active
            if (
                not filename.lower().endswith(".py")
                and dialog.get_filter() == py_filter
            ):
                filename += ".py"

            try:
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(code)
                self._set_status_message(
                    f"Code exported to {os.path.basename(filename)}", temporary=True
                )
            except Exception as e:
                # Show error dialog is better for file errors
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
                    f"Error saving file", temporary=True
                )  # Also update status bar

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
        scheme_ids = style_manager.get_scheme_ids() or []  # Handle None case
        # Store schemes with name and id for sorting and lookup
        schemes_data = []
        if scheme_ids:
            for scheme_id in scheme_ids:
                scheme = style_manager.get_scheme(scheme_id)
                if scheme:
                    schemes_data.append({"id": scheme_id, "name": scheme.get_name()})
            # Sort by name for user-friendliness
            schemes_data.sort(key=lambda x: x["name"].lower())

        # --- Setting Widgets ---

        # Draw whitespaces (as before)
        dw_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dw_label = Gtk.Label(label="Draw Whitespaces:", xalign=0.0)
        dw_switch = Gtk.Switch(
            active=self.settings.get_boolean(SETTING_DRAW_WHITESPACES)
        )
        dw_hbox.pack_start(dw_label, True, True, 0)
        dw_hbox.pack_end(dw_switch, False, False, 0)
        vbox.pack_start(dw_hbox, False, False, 0)

        # --- Add Color Scheme Dropdown ---
        cs_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cs_label = Gtk.Label(label="Color Scheme:", xalign=0.0)
        cs_combo = Gtk.ComboBoxText()

        # Populate the ComboBox
        selected_scheme_id = self.settings.get_string(SETTING_COLOR_SCHEME_ID)
        active_index = -1  # Default if not found
        for i, scheme_info in enumerate(schemes_data):
            cs_combo.append(scheme_info["id"], scheme_info["name"])  # Use id and name
            if scheme_info["id"] == selected_scheme_id:
                active_index = i

        if active_index != -1:
            cs_combo.set_active(active_index)
        elif schemes_data:  # If saved scheme not found, select first available
            cs_combo.set_active(0)
            selected_scheme_id = schemes_data[0][
                "id"
            ]  # Update the ID to save later if OK clicked

        cs_hbox.pack_start(cs_label, False, False, 0)  # Label fixed size
        cs_hbox.pack_start(cs_combo, True, True, 0)  # Combo expands
        vbox.pack_start(cs_hbox, False, False, 0)
        # --- End Color Scheme Dropdown ---

        # Tab size (as before)
        ts_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ts_label = Gtk.Label(label="Tab Size (Spaces):", xalign=0.0)
        ts_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
        ts_spin.set_value(self.settings.get_int(SETTING_TAB_SIZE))
        ts_hbox.pack_start(ts_label, True, True, 0)
        ts_hbox.pack_end(ts_spin, False, False, 0)
        vbox.pack_start(ts_hbox, False, False, 0)

        # Translate tabs to spaces (as before)
        tt_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tt_label = Gtk.Label(label="Use Spaces Instead of Tabs:", xalign=0.0)
        tt_switch = Gtk.Switch(active=self.settings.get_boolean(SETTING_TRANSLATE_TABS))
        tt_hbox.pack_start(tt_label, True, True, 0)
        tt_hbox.pack_end(tt_switch, False, False, 0)
        vbox.pack_start(tt_hbox, False, False, 0)

        # Use custom venv (as before)
        cv_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cv_label = Gtk.Label(label="Use Custom Venv:", xalign=0.0)
        cv_switch = Gtk.Switch(
            active=self.settings.get_boolean(SETTING_USE_CUSTOM_VENV)
        )
        cv_hbox.pack_start(cv_label, True, True, 0)
        cv_hbox.pack_end(cv_switch, False, False, 0)
        vbox.pack_start(cv_hbox, False, False, 0)

        # Venv path (as before)
        vp_outer_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        vp_label = Gtk.Label(label="Venv Path:", xalign=0.0)
        vp_controls_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vp_entry = Gtk.Entry(
            text=self.settings.get_string(SETTING_VENV_FOLDER),
            sensitive=cv_switch.get_active(),
            xalign=0.0,
        )
        vp_button = Gtk.Button(label="Browse...", sensitive=cv_switch.get_active())
        vp_controls_hbox.pack_start(vp_entry, True, True, 0)
        vp_controls_hbox.pack_start(vp_button, False, False, 0)
        vp_outer_hbox.pack_start(vp_label, False, False, 0)
        vp_outer_hbox.pack_start(vp_controls_hbox, True, True, 0)
        vbox.pack_start(vp_outer_hbox, False, False, 0)

        # --- Signal Handlers (as before) ---
        def _toggle_venv_widgets(
            switch, *args
        ):  # Accept potential extra args from notify::active
            is_active = switch.get_active()
            vp_entry.set_sensitive(is_active)
            vp_button.set_sensitive(is_active)

        cv_switch.connect("notify::active", _toggle_venv_widgets)
        # Initial state update
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

        # --- Run and Save ---
        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            # Save Color Scheme ID
            active_scheme_id = cs_combo.get_active_id()
            if active_scheme_id:  # Check if something is selected
                self.settings.set_string(SETTING_COLOR_SCHEME_ID, active_scheme_id)

            # Save other settings (as before)
            self.settings.set_boolean(SETTING_DRAW_WHITESPACES, dw_switch.get_active())
            self.settings.set_int(SETTING_TAB_SIZE, ts_spin.get_value_as_int())
            self.settings.set_boolean(SETTING_TRANSLATE_TABS, tt_switch.get_active())
            self.settings.set_boolean(SETTING_USE_CUSTOM_VENV, cv_switch.get_active())
            self.settings.set_string(SETTING_VENV_FOLDER, vp_entry.get_text())

            # apply_settings() will be called via the 'changed::setting-key' signal
            self.update_python_env_status()  # Explicitly update status bar now

        dialog.destroy()

    def on_settings_changed(self, settings, key):
        """Applies settings when they change via GSettings."""
        print(f"Settings changed signal received for key: {key}")
        # Apply *all* settings when any one changes.
        # This is simpler than checking the key, unless performance becomes an issue.
        self.apply_settings()
        # Update python env status only if relevant keys changed
        if key in [SETTING_USE_CUSTOM_VENV, SETTING_VENV_FOLDER]:
            self.update_python_env_status()

    def on_show_hotkeys(
        self, accel_group=None, acceleratable=None, keyval=None, modifier=None
    ):
        """Displays a list of available hotkeys in the output window."""
        hotkey_list = """
    --- Hotkeys ---
    Ctrl+R: Run Code
    Ctrl+C: Copy Code
    Ctrl+S: Export Code to File
    Ctrl+T or Ctrl+,: Open Settings
    Ctrl+H: Show Hotkeys (this list)
    ---
    """
        self.output_buffer.set_text(hotkey_list)
        # Scroll to the top to see the help message clearly
        start_iter = self.output_buffer.get_start_iter()
        self.output_view.scroll_to_iter(start_iter, 0.0, False, 0.0, 0.0)
        self._set_status_message("Hotkeys list displayed in output.", temporary=True)

    def apply_settings(self):
        """Applies current settings values to the UI."""
        print("Applying settings...")  # Debug print

        # --- Apply Color Scheme ---
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme_id = self.settings.get_string(SETTING_COLOR_SCHEME_ID)
        scheme = style_manager.get_scheme(scheme_id)

        # Fallback if saved scheme not found
        if not scheme:
            print(
                f"Warning: Saved scheme '{scheme_id}' not found. Falling back.",
                file=sys.stderr,
            )
            scheme_id = DEFAULT_STYLE_SCHEME  # Use constant default
            scheme = style_manager.get_scheme(scheme_id)
            if not scheme:  # Fallback further if default is also missing
                scheme_id = "classic"
                scheme = style_manager.get_scheme(scheme_id)

        if scheme:
            self.code_buffer.set_style_scheme(scheme)
            print(f"Applied color scheme: {scheme_id}")
        else:
            print(
                f"Warning: Could not apply any color scheme (tried: {self.settings.get_string(SETTING_COLOR_SCHEME_ID)}, {DEFAULT_STYLE_SCHEME}, classic).",
                file=sys.stderr,
            )
        # --- End Apply Color Scheme ---

        # Draw whitespaces (as before)
        if self.settings.get_boolean(SETTING_DRAW_WHITESPACES):
            self.space_drawer.set_types_for_locations(
                GtkSource.SpaceLocationFlags.ALL,
                GtkSource.SpaceTypeFlags.SPACE | GtkSource.SpaceTypeFlags.TAB,
            )
        else:
            self.space_drawer.set_types_for_locations(
                GtkSource.SpaceLocationFlags.ALL, 0
            )
        print(
            f"Applied draw whitespaces: {self.settings.get_boolean(SETTING_DRAW_WHITESPACES)}"
        )

        # Tab size and translation (as before)
        tab_size = self.settings.get_int(SETTING_TAB_SIZE)
        translate_tabs = self.settings.get_boolean(SETTING_TRANSLATE_TABS)
        self.code_input.set_tab_width(tab_size)
        self.code_input.set_insert_spaces_instead_of_tabs(translate_tabs)
        print(f"Applied tab size: {tab_size}, translate tabs: {translate_tabs}")

        # Refresh the view to make sure changes like tab width are visible
        self.code_input.queue_draw()
        print("Settings applied complete.")

    def get_python_interpreter(self):
        """Determines the Python interpreter path based on settings."""
        if self.settings.get_boolean(SETTING_USE_CUSTOM_VENV):
            venv_folder = self.settings.get_string(SETTING_VENV_FOLDER)
            if venv_folder and os.path.isdir(venv_folder):
                # Standard venv structure
                python_executable = os.path.join(venv_folder, "bin", "python")
                python3_executable = os.path.join(venv_folder, "bin", "python3")
                # Prefer python3 if it exists, otherwise fall back to python
                if os.path.exists(python3_executable):
                    return python3_executable
                elif os.path.exists(python_executable):
                    return python_executable
                else:
                    # Invalid venv path, fall back to system python
                    print(
                        f"Warning: No 'python' or 'python3' found in {os.path.join(venv_folder, 'bin')}. Falling back to system.",
                        file=sys.stderr,
                    )
                    pass  # Fall through to system python
            else:
                print(
                    f"Warning: Custom venv path '{venv_folder}' is invalid. Falling back to system.",
                    file=sys.stderr,
                )
                pass  # Fall through to system python

        # Default to system python3 or python
        # Check if python3 exists, otherwise use python
        try:
            # Use shell=False (default and safer), pass args as list
            subprocess.run(
                ["python3", "--version"], check=True, capture_output=True, text=True
            )
            return "python3"
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                subprocess.run(
                    ["python", "--version"], check=True, capture_output=True, text=True
                )
                return "python"
            except (FileNotFoundError, subprocess.CalledProcessError):
                print(
                    "Warning: Neither 'python3' nor 'python' found in PATH.",
                    file=sys.stderr,
                )
                return "python"  # Return as a last resort, execution will likely fail

    def update_python_env_status(self):
        """Updates the status bar with the current Python environment info."""
        python_interpreter = self.get_python_interpreter()
        python_version = "Unknown"
        try:
            # Use a short timeout in case the interpreter hangs
            result = subprocess.run(
                [python_interpreter, "--version"],
                capture_output=True,
                text=True,
                check=False,  # Don't raise exception
                timeout=2,  # Short timeout
            )
            if result.returncode == 0:
                # stderr often contains the version for `python --version`
                version_output = result.stdout.strip() or result.stderr.strip()
                if version_output.startswith("Python "):
                    python_version = version_output.split()[1]
                else:
                    python_version = version_output  # Use whatever was printed
            else:
                print(
                    f"Warning: Failed to get version from '{python_interpreter}'. stderr: {result.stderr}",
                    file=sys.stderr,
                )

        except FileNotFoundError:
            python_version = "Not Found"
        except subprocess.TimeoutExpired:
            python_version = "Timeout"
        except Exception as e:
            print(f"Error checking Python version: {e}", file=sys.stderr)
            python_version = "Error"

        status_text = ""
        if self.settings.get_boolean(SETTING_USE_CUSTOM_VENV):
            venv_folder = self.settings.get_string(SETTING_VENV_FOLDER)
            if venv_folder:
                # Show just the venv folder name for brevity
                status_text = f"Custom: {venv_folder} ({python_version})"
            else:
                status_text = "Custom venv selected but path not set"
        else:
            status_text = f"System Python ({python_version})"

        self._set_status_message(status_text)

    def _set_status_message(self, text, temporary=False):
        """Updates the status bar label, optionally resetting after a delay."""
        # Cancel any existing timeout
        if self._status_timeout_id:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None

        self.status_label.set_text(text)

        if temporary:
            # Set a new timeout to restore the default status
            self._status_timeout_id = GLib.timeout_add(
                STATUS_MESSAGE_TIMEOUT_MS, self._restore_default_status
            )

    def _restore_default_status(self):
        """Restores the status bar to the default (Python env info). Called by timeout."""
        self.update_python_env_status()
        self._status_timeout_id = None  # Clear the ID
        return GLib.SOURCE_REMOVE  # Stop the timeout


def main():
    PythonRunnerApp()
    Gtk.main()


# --- Main Execution ---
if __name__ == "__main__":
    # Set Application ID for proper desktop integration (window grouping, etc.)
    main()
