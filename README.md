Basic BIOS Settings Modifier using SCEWIN

This Python project is designed to change multiple BIOS (NVRAM) settings quickly and easily — modifying 500+ settings in just seconds.

It works by reading a configuration file and applying the changes using **SCEWIN**.

Configuration File Format
The settings file should be a `.txt` file, with each line following this format:

```
"SettingName" 0xValue
```

**Example:**
```
"C6DRAM" 0x0
"ASPM" 0x0
```

- **SettingName**: The name of the BIOS setting as shown in your `nvram.txt` export from SCEWIN.
- **0xValue**: The hexadecimal value to set for that option (e.g., `0x0` to disable, `0x1` to enable).
- You can find the available values and their meanings in your exported `nvram.txt` file from SCEWIN.

Works with AMD / Intel. 

<img width="691" height="789" alt="Project_PICTURE" src="https://github.com/user-attachments/assets/5bb6bb4b-28bb-471b-917e-3ff896eb86c4" />
