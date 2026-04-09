# Thorlabs APT Stage Controller

A modern PyQt5-based GUI application for controlling Thorlabs TDC001 motor controllers with MTS50/M stages. This replaces the legacy Thorlabs APT software with a clean, intuitive interface.

## Features

- **Multi-Motor Control**: Control up to 3 motors simultaneously
- **Real-time Position Monitoring**: Live position updates and status indicators
- **Precise Movement Control**: 
  - Absolute positioning
  - Relative movements
  - Jog controls
  - Home positioning
- **Configurable Parameters**: Adjustable velocity and acceleration settings
- **Device Auto-Discovery**: Automatic detection of connected Thorlabs devices
- **Modern UI**: Clean PyQt5 interface with color-coded motor panels

## Requirements

- Python 3.x
- PyQt5
- thorlabs_apt_device

## Installation

### Windows

Run the installation script:
```batch
INSTALL.bat
```

This will:
1. Create a Python virtual environment
2. Install all required dependencies
3. Set up the application

## Usage

### Running the Application

**Windows:**
```batch
RUN.bat
```

**Manual:**
```bash
python main.py
```

### Building Standalone Executable (Windows)

```batch
build_windows.bat
```

This creates a standalone `.exe` file in the `dist` folder.

## Project Structure

- `main.py` - Application entry point
- `gui.py` - PyQt5 GUI implementation
- `devices.py` - Thorlabs device communication layer
- `INSTALL.bat` - Windows installation script
- `RUN.bat` - Windows run script
- `build_windows.bat` - Windows build script for creating executable

## Motor Specifications

- **Travel Range**: 0-50 mm
- **Maximum Velocity**: 2.6 mm/s
- **Maximum Acceleration**: 4.0 mm/s²
- **Minimum Step**: 0.001 mm

## License

This project is open source and available for use and modification.

## Author

Created as a replacement for legacy Thorlabs APT software.
