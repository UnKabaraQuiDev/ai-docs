#!/usr/bin/env python3

import os
import re
import javalang
import sys
from openai import OpenAI
from pygments import highlight
from pygments.lexers import JavaLexer
from pygments.formatters import TerminalFormatter
from dotenv import load_dotenv
from pathlib import Path

script_dir = Path(__file__).resolve().parent
load_dotenv(dotenv_path=script_dir / '.env')
client = OpenAI()

def read_java_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        return file.read()

def write_java_file(file_path, content):
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(content)

def is_getter(method):
    name = method.name
    return (
        (name.startswith('get') or name.startswith('is')) and
        len(method.parameters) == 0 and
        method.return_type is not None
    )

def is_setter(method):
    name = method.name
    return (
        name.startswith('set') and
        len(method.parameters) == 1 and
        (method.return_type is None or str(method.return_type) == 'void')
    )

def get_method_positions(java_code):
    tree = javalang.parse.parse(java_code)
    method_positions = []

    for path, node in tree:
        if isinstance(node, javalang.tree.MethodDeclaration):
            if is_getter(node) or is_setter(node):
                continue  # Skip getters and setters

            # Build class hierarchy
            class_hierarchy = []
            for ancestor in path:
                if isinstance(ancestor, javalang.tree.ClassDeclaration):
                    modifiers = ' '.join(ancestor.modifiers) if ancestor.modifiers else ''
                    class_hierarchy.append(f"{modifiers} class {ancestor.name}".strip())
            full_hierarchy = ' > '.join(class_hierarchy)

            method_positions.append({
                'name': node.name,
                'position': node.position,
                'parameters': node.parameters,
                'return_type': node.return_type,
                'modifiers': node.modifiers,
                'hierarchy': full_hierarchy
            })

    return method_positions

def has_javadoc(java_code_lines, line_number):
    """
    Check if there is a JavaDoc comment immediately above the given line number.
    """
    index = line_number - 2  # Convert to 0-based index and move one line up
    while index >= 0:
        line = java_code_lines[index].strip()
        if line == '':
            index -= 1
            continue
        if line.startswith('/**'):
            return True
        if line.startswith('//') or line.startswith('/*'):
            return False
        break
    return False

def print_highlighted_java_code(code):
    highlighted = highlight(code, JavaLexer(), TerminalFormatter())
    print(highlighted)

def prompt_user_for_description(class_hierarchy, method_name, method_code, java_code_lines, line_number):
    print(f"\nNo JavaDoc found for method '{method_name}' ({class_hierarchy}).")
    print("\n ------- ================== ------- \n")
    print_highlighted_java_code(method_code)
    print("\n ------- ================== ------- \n")
    description = input(f"Please provide a brief description for the method '{method_name}': ")
    return description

def generate_javadoc(class_hierarchy, method_code, user_description):
    prompt = f"""Generate a JavaDoc comment for the following Java method. The method is situated in: `{class_hierarchy}` performs the following: {user_description}

Method:
```
{method_code}
```

JavaDoc:"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": """You are a Java documentation assistant, use proper JavaDoc notation ({@link}, etc). Thrown ResponseStatusException (HTTPS errors) are seen as a return type, use this format to bypass this limitation (custom returns) (this is an example):
/**
* <detailed description>
* 
* <br>
* <br>
* <b>Returns:</b>
* <ul>
* <li>{@link HttpStatus#BAD_REQUEST} Invalid language.</li>
* <li>{@link HttpStatus#BAD_REQUEST} Invalid email.</li>
* <li>{@link HttpStatus#BAD_REQUEST} Invalid source.</li>
* </ul>
* 
* @return {@link HttpStatus#ACCEPTED} : {@link QuestionResponse}
*/

If no Http error is returned, use this format:
/**
* <detailed description>
*
* <params, if present>
* @return {@link HttpStatus#ACCEPTED} : {@link <type>}
*/

Do not specify if it returns a Void type.
Put the javadoc elements in this order: <description> <custom returns> <params> <returns> <throws> <return>.
Only specify http errors in the <custom returns> section, for regular @returns, use standard JavaDoc."""},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=200
        )
        raw_javadoc = response.choices[0].message.content.strip()
        # Clean up: remove excessive empty lines and whitespace
        lines = [line.strip() for line in raw_javadoc.splitlines() if line.strip() != '']
        javadoc = '\n'.join(lines)

        return javadoc
    except Exception as e:
        print(f"Error generating JavaDoc: {e}")
        return None

def insert_javadoc(java_code_lines, line_number, javadoc):
    """
    Insert the JavaDoc comment above the specified line number, with proper indentation.
    """
    insert_at = line_number - 1
    while insert_at > 0:
        line = java_code_lines[insert_at - 1].strip()
        if line.startswith("@"):
            insert_at -= 1
        else:
            break

    # Detect indentation of the method line
    method_line = java_code_lines[line_number - 1]
    indent_match = re.match(r'^(\s*)', method_line)
    indent = indent_match.group(1) if indent_match else ''

    # Prepare indented javadoc lines
    javadoc_lines = [f"{indent}{line.strip()}" for line in javadoc.split('\n') if line.strip()]

    # Insert a blank line before for separation
    return java_code_lines[:insert_at] + javadoc_lines + java_code_lines[insert_at:]

def extract_full_method_code(java_code_lines, start_line):
    """
    Extracts the complete method code block from the Java source lines,
    starting from `start_line` using curly brace matching.
    """
    start_idx = start_line - 1  # Convert to 0-based index
    method_lines = []
    open_braces = 0
    started = False

    for i in range(start_idx, len(java_code_lines)):
        line = java_code_lines[i]
        method_lines.append(line)

        # Count braces to detect method body
        open_braces += line.count('{')
        open_braces -= line.count('}')

        if '{' in line:
            started = True

        if started and open_braces == 0:
            break

    return '\n'.join(method_lines)


def main():
    if len(sys.argv) > 1:
        java_file_path = sys.argv[1]
    else:
        java_file_path = input("Enter the path to the .java file: ").strip()

    if not os.path.isfile(java_file_path):
        print("File not found.")
        return

    java_code = read_java_file(java_file_path)
    java_code_lines = java_code.split('\n')
    method_positions = get_method_positions(java_code)
    # we start from the end
    method_positions.sort(key=lambda m: m['position'].line, reverse=True)

    for method in method_positions:
        line_number = method['position'].line
        if not has_javadoc(java_code_lines, line_number):
            # Extract method code (simple approach: take 10 lines starting from method declaration)
            method_code_snippet = extract_full_method_code(java_code_lines, line_number)
            user_description = prompt_user_for_description(method['hierarchy'], method['name'], method_code_snippet, java_code_lines, line_number)
            javadoc = generate_javadoc(method['hierarchy'], method_code_snippet, user_description)
            if javadoc:
                java_code_lines = insert_javadoc(java_code_lines, line_number, javadoc)
                print(f"JavaDoc added for method '{method['name']}'.")
                print("\n ------- ================== ------- \n")
                print_highlighted_java_code(javadoc)
                print("\n ------- ================== ------- \n")
            else:
                print(f"Failed to generate JavaDoc for method '{method['name']}'.")

    updated_java_code = '\n'.join(java_code_lines)
    write_java_file(java_file_path, updated_java_code)
    print(f"\nUpdated Java file saved to {java_file_path}")

if __name__ == "__main__":
    main()
