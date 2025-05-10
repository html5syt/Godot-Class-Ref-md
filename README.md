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

## Konwn issues
1. The tool maybe output some not-translated strings in the markdown files. You can translate them by yourself or change the `SIMILARITY_THRESHOLD` in `main.py` to a smaller value to make the tool more accurate.
2. The codeblocks in the markdown files may not be correctly formatted. They lose the tab indents because some BUGs. You can use a code formatter like Prettier in your code editor or AI tools to format them.