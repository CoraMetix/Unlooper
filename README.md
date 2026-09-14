# Unlooper

This code is for unlooping gcode and rendering an image

## Table of Contents

* [General Info](#general-info)
* [Functions](#function)
* [Setup](#setup)

## General Info

This software is designed to unloop M98 gcode into linear code that can be read by any gcode system. This system is primarily optimised for 2D x and y co-ordinates with empahsis on G1, G2 and G3 commands using the G90 and G91 co-ordinate systems. This software can be used either by command line or by running in python editor

## Function

Python editor:
To run using a python editor you can edit either of these variables
```
# Commands you can edit
# Setting to 0 will unloop and generate an image
# Setting to 1 will unloop only and not generate an image
variables["unloop_only"] = 1

# Everything must contain forward slashes only
file_name = "TXT Files/DO_4 x10.txt"
```

Change the filename to the path from the current folder to the gcode file to be read preferably the file to be read and the unlooper are in the same folder or the gcode file is placed inside a sub folder with the unlooper being in the main folder. An output folder will be created that will contain a folder with the same name as the gcode file that was unlooped. This folder will contain the unlooped code and image generated (if requested)


```
Random Folder
  ├── unlooper.py
  ├── TXT_FILES
  │   ├── filename.gcode
  ├── Output
  │   ├── filename
  |   │   ├── filename_unlooped.txt
```

Command line
This program can also be run from the command line using 
```
python Unlooper.py "filename" <unloop_only> [feedrate_mm_min] [flowrate_mg_min] [render_mode]
```
where "filename" is replaced with the filepath and `<unloop_only>` is replaced with either 1 or 0.

* `feedrate_mm_min` / `flowrate_mg_min` – optional overrides for the time / material estimate (0 or omitted = use the file's own values).
* `render_mode` – optional, one of:
  * `precise` (default) – the exact full-resolution raster render (`..._cv2_Image_output.png`). Accounts for fibre width and pass overlap; render time scales with part area.
  * `preview` – a fast, NCViewer-style **vector** render (`..._preview.svg`). No line thickness and no pass-overlap colouring, but also no resolution ceiling: arcs are exact SVG arcs (never flattened into a polyline) and lines stay hairline-thin at any zoom level, so you can zoom in as far as the toolpath data actually resolves - this is the one to reach for when you need to inspect micron-scale pores. Open it in any browser, or view it directly in the GUI (below).
  * `both` – write the precise image and the preview. The preview is written first so it's available almost immediately.
  * `none` – skip rendering; only produce the unlooped code and the timing / material numbers.

When running from a python editor, set `variables["render_mode"]` near the top of the script instead.

## GUI

`python Unlooper_gui.py` provides a front-end with a file queue, the override fields, and check-boxes for **Precise image** and **Fast preview** (tick either, both, or neither). Both are shown in the same pan/zoom viewer - scroll to zoom, drag to pan, double-click (or the **Fit** button) to reset the view - so the preview's SVG genuinely does zoom as far in as NCViewer does, right in the app. When both are enabled the preview appears first and is replaced by the precise image once it finishes. An **Open Preview in Browser** button opens the same `.svg` in your default browser, useful for a bigger window or for sharing the file.

## Setup

Download the file and place in the chosen folder
Make sure to download and run the requriements.txt file (navigate to the folder first in cmd prompt)
```
pip install -r requirements.txt
```

## Example image from output

![Custom_complex_scaffold_SR1 0_Raw_image_output](https://github.com/user-attachments/assets/f256c392-e3ee-4ad2-af75-4a53d4f6fc08)

