Middleware Service Setup (Windows)

This document explains how to create a middleware executable and install it as a Windows service using PyInstaller and NSSM.

Prerequisites:

* Windows operating system
* Python installed and added to PATH
* PyInstaller installed (pip install pyinstaller)
* NSSM installed and available in PATH
* Administrator privileges

Step 1: Create Executable

Run the following command to build a standalone executable:

pip install pyinstaller

pyinstaller --onefile app.py

After the build completes, test the executable:

dist/app.exe

Step 2: Install Windows Service Using NSSM

Create a Windows service named MyfuelaiConnector:

nssm install MyfuelaiConnector

Configure the service with the following values:

Path:

myfuelai_datarelay\fastapi_listener\src\pdi\installer\middleware.py

Startup directory:

myfuelai_datarelay\fastapi_listener\src\pdi\installer

Arguments:

(empty)

Delay:

5000 ms

Save the service configuration.

Step 3: Start the Service

Start the service using:

net start MyfuelaiConnector

Step 4: Verify Service Status

Check the service status:

sc query MyfuelaiConnector

Step 5: Service Management Commands

nssm restart MyfuelaiConnector

nssm status MyfuelaiConnector

nssm stop MyfuelaiConnector

nssm start MyfuelaiConnector

nssm remove MyfuelaiConnector

Notes:

* All commands must be run as Administrator.
* Ensure paths are correct before installing the service.
* If the service fails to start, check the Windows Event Viewer for error logs.
* Restart the service after updating the executable.
