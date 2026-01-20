import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import re
import os


class NVRAMEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("BIOS Settings Modifier - by Mushroom")
        self.root.geometry("700x600")
        self.root.configure(bg='#1a1a1a')
        self.root.resizable(True, True)

        # Configure modern dark theme colors
        self.bg_color = '#1a1a1a'
        self.bg_secondary = '#2d2d2d'
        self.fg_color = '#ffffff'
        self.accent_color = '#ff4444'  # Red accent
        self.accent_hover = '#ff6666'
        self.entry_bg = '#353535'
        self.button_bg = '#404040'
        self.success_color = '#44ff44'
        self.warning_color = '#ffaa44'

        # Configure ttk styles for modern theme
        self.style = ttk.Style()
        self.style.theme_use('clam')

        # Configure modern styles
        self.style.configure('Modern.TFrame', background=self.bg_color, borderwidth=0)
        self.style.configure('Card.TFrame', background=self.bg_secondary, relief='flat', borderwidth=1)
        self.style.configure('Modern.TLabel', background=self.bg_color, foreground=self.fg_color, font=('Segoe UI', 10))
        self.style.configure('Title.TLabel', background=self.bg_color, foreground=self.accent_color,
                             font=('Segoe UI', 16, 'bold'))
        self.style.configure('Subtitle.TLabel', background=self.bg_color, foreground=self.fg_color,
                             font=('Segoe UI', 12, 'bold'))
        self.style.configure('Info.TLabel', background=self.bg_color, foreground='#888888', font=('Segoe UI', 9))

        # Red accent buttons
        self.style.configure('Accent.TButton',
                             background=self.accent_color,
                             foreground='white',
                             font=('Segoe UI', 10, 'bold'),
                             borderwidth=0,
                             focuscolor='none',
                             relief='flat')
        self.style.map('Accent.TButton',
                       background=[('active', self.accent_hover), ('pressed', '#dd3333')])

        # Secondary buttons
        self.style.configure('Secondary.TButton',
                             background=self.button_bg,
                             foreground=self.fg_color,
                             font=('Segoe UI', 9),
                             borderwidth=0,
                             focuscolor='none',
                             relief='flat')
        self.style.map('Secondary.TButton',
                       background=[('active', '#505050'), ('pressed', '#303030')])

        # Modern treeview
        self.style.configure('Modern.Treeview',
                             background=self.entry_bg,
                             foreground=self.fg_color,
                             fieldbackground=self.entry_bg,
                             borderwidth=0,
                             font=('Segoe UI', 9))
        self.style.configure('Modern.Treeview.Heading',
                             background=self.button_bg,
                             foreground=self.fg_color,
                             font=('Segoe UI', 10, 'bold'),
                             borderwidth=0)

        self.settings_file = ""
        self.nvram_file = ""
        self.settings_data = {}

        self.create_widgets()

    def create_widgets(self):
        # Main container with padding
        main_container = tk.Frame(self.root, bg=self.bg_color)
        main_container.pack(fill='both', expand=True, padx=20, pady=20)

        # Header section
        header_frame = tk.Frame(main_container, bg=self.bg_color)
        header_frame.pack(fill='x', pady=(0, 20))

        # Title and creator
        title_label = ttk.Label(header_frame, text="BIOS Settings Modifier", style='Title.TLabel')
        title_label.pack(anchor='w')

        creator_label = ttk.Label(header_frame, text="Created by Mushroom6.6", style='Info.TLabel')
        creator_label.pack(anchor='w', pady=(0, 5))

        # File selection card
        file_card = ttk.Frame(main_container, style='Card.TFrame', padding=20)
        file_card.pack(fill='x', pady=(0, 15))

        ttk.Label(file_card, text="File Selection", style='Subtitle.TLabel').pack(anchor='w', pady=(0, 15))

        # Settings file row
        settings_row = tk.Frame(file_card, bg=self.bg_secondary)
        settings_row.pack(fill='x', pady=(0, 10))

        ttk.Label(settings_row, text="Settings File:", style='Modern.TLabel').pack(side='left')
        self.settings_label = ttk.Label(settings_row, text="No file selected", style='Info.TLabel')
        self.settings_label.pack(side='left', padx=(10, 0))

        settings_btn = ttk.Button(settings_row, text="Browse Settings",
                                  command=self.browse_settings, style='Secondary.TButton')
        settings_btn.pack(side='right')

        # NVRAM file row
        nvram_row = tk.Frame(file_card, bg=self.bg_secondary)
        nvram_row.pack(fill='x')

        ttk.Label(nvram_row, text="NVRAM File:", style='Modern.TLabel').pack(side='left')
        self.nvram_label = ttk.Label(nvram_row, text="No file selected", style='Info.TLabel')
        self.nvram_label.pack(side='left', padx=(10, 0))

        nvram_btn = ttk.Button(nvram_row, text="Browse NVRAM",
                               command=self.browse_nvram, style='Secondary.TButton')
        nvram_btn.pack(side='right')

        # Settings preview card
        preview_card = ttk.Frame(main_container, style='Card.TFrame', padding=20)
        preview_card.pack(fill='both', expand=True, pady=(0, 15))

        preview_header = tk.Frame(preview_card, bg=self.bg_secondary)
        preview_header.pack(fill='x', pady=(0, 10))

        ttk.Label(preview_header, text="Settings Preview", style='Subtitle.TLabel').pack(side='left')
        self.count_label = ttk.Label(preview_header, text="0 settings loaded", style='Info.TLabel')
        self.count_label.pack(side='right')

        # Treeview with custom styling
        tree_frame = tk.Frame(preview_card, bg=self.bg_secondary)
        tree_frame.pack(fill='both', expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("Value",), height=8, style='Modern.Treeview')
        self.tree.heading("#0", text="Setting Name")
        self.tree.heading("Value", text="Value")
        self.tree.column("#0", width=400)
        self.tree.column("Value", width=150)

        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # Process button
        button_frame = tk.Frame(main_container, bg=self.bg_color)
        button_frame.pack(fill='x', pady=(0, 15))

        self.process_button = ttk.Button(button_frame, text="Process NVRAM File",
                                         command=self.process_nvram, state="disabled", style='Accent.TButton')
        self.process_button.pack(side='left')

        # Status indicator
        self.status_indicator = ttk.Label(button_frame, text="Ready", style='Info.TLabel')
        self.status_indicator.pack(side='right')

        # Log card
        log_card = ttk.Frame(main_container, style='Card.TFrame', padding=20)
        log_card.pack(fill='x')

        log_header = tk.Frame(log_card, bg=self.bg_secondary)
        log_header.pack(fill='x', pady=(0, 10))

        ttk.Label(log_header, text="Process Log", style='Subtitle.TLabel').pack(side='left')

        clear_btn = ttk.Button(log_header, text="Clear Log",
                               command=self.clear_log, style='Secondary.TButton')
        clear_btn.pack(side='right')

        # Status text with modern styling
        self.status_text = tk.Text(log_card, height=6, width=70,
                                   bg=self.entry_bg, fg=self.fg_color,
                                   insertbackground=self.fg_color,
                                   selectbackground='#555555',
                                   font=('Consolas', 9),
                                   borderwidth=0,
                                   relief='flat')
        self.status_text.pack(fill='x')

        # Configure grid weights for responsiveness
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

    def clear_log(self):
        self.status_text.delete(1.0, tk.END)

    def browse_settings(self):
        filename = filedialog.askopenfilename(
            title="Select Settings File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if filename:
            self.settings_file = filename
            self.settings_label.config(text=os.path.basename(filename), foreground=self.success_color)
            self.load_settings()
            self.check_ready()

    def browse_nvram(self):
        filename = filedialog.askopenfilename(
            title="Select NVRAM File",
            filetypes=[("All files", "*.*"), ("Binary files", "*.bin"), ("Text files", "*.txt")]
        )
        if filename:
            self.nvram_file = filename
            self.nvram_label.config(text=os.path.basename(filename), foreground=self.success_color)
            self.check_ready()

    def load_settings(self):
        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Clear previous data
            self.settings_data = {}
            for item in self.tree.get_children():
                self.tree.delete(item)

            # Parse settings - format: "Setting Name" 0xValue
            pattern = r'"([^"]+)"\s+(0x[0-9a-fA-F]+)'
            matches = re.findall(pattern, content)

            for setting_name, value in matches:
                self.settings_data[setting_name] = value
                self.tree.insert("", "end", text=setting_name, values=(value,))

            count = len(self.settings_data)
            self.count_label.config(text=f"{count} settings loaded")
            self.log(f"Loaded {count} settings from file", "success")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load settings file: {str(e)}")
            self.log(f"Error loading settings: {str(e)}", "error")

    def check_ready(self):
        if self.settings_file and self.nvram_file and self.settings_data:
            self.process_button.config(state="normal")
            self.status_indicator.config(text="Ready to process", foreground=self.success_color)
        else:
            self.process_button.config(state="disabled")
            self.status_indicator.config(text="Select files first", foreground=self.warning_color)

    def log(self, message, type="info"):
        timestamp = __import__('datetime').datetime.now().strftime("%H:%M:%S")

        if type == "success":
            prefix = ""
        elif type == "error":
            prefix = ""
        elif type == "warning":
            prefix = ""
        else:
            prefix = ""

        formatted_message = f"[{timestamp}] {prefix} {message}\n"
        self.status_text.insert(tk.END, formatted_message)
        self.status_text.see(tk.END)
        self.root.update()

    def process_nvram(self):
        try:
            self.log("Starting NVRAM processing...", "info")
            self.status_indicator.config(text="Processing...", foreground=self.warning_color)

            # Try to read NVRAM file as text first
            nvram_content = ""
            is_binary = False

            try:
                with open(self.nvram_file, 'r', encoding='utf-8') as f:
                    nvram_content = f.read()
                self.log("NVRAM file detected as text format", "info")
            except UnicodeDecodeError:
                # File is binary, try to read with different encodings or as binary
                self.log("NVRAM file appears to be binary, attempting to read...", "info")
                try:
                    with open(self.nvram_file, 'r', encoding='latin-1') as f:
                        nvram_content = f.read()
                    is_binary = True
                    self.log("Successfully read binary NVRAM file", "success")
                except Exception as e:
                    self.log(f"Failed to read binary file with latin-1: {e}", "warning")
                    # Try reading as binary and converting
                    with open(self.nvram_file, 'rb') as f:
                        binary_data = f.read()

                    # Try to find text sections in binary data
                    try:
                        nvram_content = binary_data.decode('utf-8', errors='ignore')
                        is_binary = True
                        self.log("Read binary file with UTF-8 (ignoring errors)", "success")
                    except:
                        # Last resort: convert binary to latin-1
                        nvram_content = binary_data.decode('latin-1')
                        is_binary = True
                        self.log("Read binary file as latin-1", "success")

            self.log(f"Original file size: {len(nvram_content)} characters", "info")

            total_changes = 0

            # Process each setting
            for setting_name, desired_value in self.settings_data.items():
                self.log(f"Processing: {setting_name} -> {desired_value}", "info")

                # Convert hex value to integer
                try:
                    value_int = int(desired_value, 16)
                    value_hex_string = f"{value_int:02X}"  # Format as 2-digit hex
                except ValueError:
                    self.log(f"Invalid hex value {desired_value}", "error")
                    continue

                # Find all occurrences of this setting
                setting_pattern = re.escape(setting_name)
                matches = list(re.finditer(setting_pattern, nvram_content))

                if not matches:
                    self.log(f"Setting not found in NVRAM file", "warning")
                    continue

                self.log(f"Found {len(matches)} instances", "info")

                # Process each match
                changes_made = 0
                offset_adjustment = 0  # Track changes in string length

                for match in matches:
                    start_pos = match.start() + offset_adjustment

                    # Find the setup question block containing this setting
                    block_start = nvram_content.rfind("Setup Question", 0, start_pos + len(setting_name))
                    if block_start == -1:
                        continue

                    # Find the end of this block (next "Setup Question" or end of file)
                    next_block = nvram_content.find("Setup Question", block_start + 1)
                    block_end = next_block if next_block != -1 else len(nvram_content)

                    block_content = nvram_content[block_start:block_end]

                    # Find the Options section in this block
                    options_match = re.search(r'Options\s*=(.+?)(?=\n\n|\nSetup Question|\Z)',
                                              block_content, re.DOTALL)

                    if not options_match:
                        continue

                    options_section = options_match.group(1)

                    # Remove existing * markers
                    options_section = re.sub(r'\*\[', '[', options_section)

                    # Find the option that matches our desired value
                    option_pattern = r'\[([0-9A-Fa-f]+)\]([^\n\[\]]*)'
                    option_matches = re.findall(option_pattern, options_section)

                    updated_options = options_section
                    option_found = False

                    for opt_value, opt_text in option_matches:
                        opt_value_int = int(opt_value, 16) if opt_value.isdigit() or all(
                            c in '0123456789ABCDEFabcdef' for c in opt_value) else int(opt_value)

                        if opt_value_int == value_int:
                            # Add * before this option
                            old_pattern = f'[{opt_value}]{opt_text}'
                            new_pattern = f'*[{opt_value}]{opt_text}'
                            updated_options = updated_options.replace(old_pattern, new_pattern)
                            option_found = True
                            break

                    if option_found:
                        # Replace the options section in the block
                        new_block_content = block_content.replace(options_match.group(1), updated_options)

                        # Replace the block in the full content
                        old_content_len = len(nvram_content)
                        nvram_content = nvram_content[:block_start] + new_block_content + nvram_content[block_end:]

                        # Update offset adjustment
                        offset_adjustment += len(nvram_content) - old_content_len

                        changes_made += 1
                        self.log(f" Updated instance {changes_made}", "success")
                    else:
                        self.log(f" Could not find option value {desired_value}", "warning")

                total_changes += changes_made
                self.log(f" Total changes for this setting: {changes_made}", "info")

            if total_changes > 0:
                # Save the modified file
                save_path = filedialog.asksaveasfilename(
                    title="Save Modified NVRAM File",
                    defaultextension=".txt" if not is_binary else "",
                    filetypes=[("All files", "*.*"), ("Text files", "*.txt"), ("Binary files", "*.bin")]
                )

                if save_path:
                    if is_binary:
                        # Save as binary with original encoding
                        with open(save_path, 'w', encoding='latin-1') as f:
                            f.write(nvram_content)
                    else:
                        # Save as UTF-8 text
                        with open(save_path, 'w', encoding='utf-8') as f:
                            f.write(nvram_content)

                    self.log(f" File saved successfully: {os.path.basename(save_path)}", "success")
                    self.log(f" Total changes made: {total_changes}", "success")
                    self.status_indicator.config(text="Processing complete!", foreground=self.success_color)
                    messagebox.showinfo("Success! 🎉",
                                        f"NVRAM file processed successfully!\n\nTotal changes: {total_changes}\nSaved to: {os.path.basename(save_path)}")
                else:
                    self.log(" Save cancelled by user", "warning")
                    self.status_indicator.config(text="Save cancelled", foreground=self.warning_color)
            else:
                self.log("No changes were made to the file", "warning")
                self.status_indicator.config(text="No changes made", foreground=self.warning_color)
                messagebox.showwarning("No Changes", "No changes were made to the NVRAM file")

        except Exception as e:
            error_msg = f"Error processing NVRAM file: {str(e)}"
            self.log(f" {error_msg}", "error")
            self.status_indicator.config(text="Processing failed", foreground='#ff4444')
            messagebox.showerror("Error", error_msg)


def main():
    root = tk.Tk()

    # Set window icon (if you have an icon file)
    try:
        root.iconbitmap('icon.ico')  # Optional: add your icon file
    except:
        pass

    app = NVRAMEditor(root)
    root.mainloop()


if __name__ == "__main__":
    main()





