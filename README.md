# Godot Engine Class Reference markdown Generator
This is a tool to generate markdown files for the Godot Engine Class Reference. It uses the Godot Engine's XML documentation file to generate the markdown files.

## Usage
1. Clone the repository.
2. Install `uv`.
3. run `uv run main.py` in the terminal.

## PO file
The PO file is used to translate the markdown files into different languages. 

You can replace the po file with your own translations or download from weblate.

And then change the `translator ="godot-engine-godot-class-reference-zh_Hans.po"` in the `main.py` file to your own po file path.

Then translate the `LOCALIZED_STRINGS` and `DOCS_URL` in `main.py` file to your own language.