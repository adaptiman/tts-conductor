# Automatic File Changes Troubleshooting

If you're experiencing automatic changes to files after committing (especially whitespace changes), here are the most common causes and solutions:

## Common Causes

### 1. VS Code Auto-Formatting
- **Format on Save**: `"editor.formatOnSave": true`
- **Trim Trailing Whitespace**: `"files.trimTrailingWhitespace": true` 
- **Auto Whitespace**: `"editor.trimAutoWhitespace": true`

### 2. Git Hooks
- Pre-commit hooks running formatters
- Post-commit hooks
- Check `.git/hooks/` directory

### 3. VS Code Extensions
- Markdown formatters
- Prettier
- Auto-formatting extensions

### 4. Editor Config
- `.editorconfig` files with trim settings
- Language-specific formatting rules

## Solutions Implemented

### 1. Updated VS Code Settings (`.vscode/settings.json`)
```json
{
    "editor.formatOnSave": false,
    "editor.trimAutoWhitespace": false,
    "files.trimTrailingWhitespace": false,
    "[markdown]": {
        "editor.formatOnSave": false,
        "editor.trimAutoWhitespace": false,
        "files.trimTrailingWhitespace": false
    }
}
```

### 2. Created `.editorconfig`
- Disables trailing whitespace trimming for Markdown files
- Maintains consistent behavior across editors

### 3. Manual Control
- Use `./lint.sh` for intentional formatting
- Use `./format-modified.sh` for changed files only

## Testing the Fix

1. Make a small change to README.md
2. Save the file
3. Commit the change  
4. Check if automatic changes still occur

If problems persist, check:
- Global VS Code settings (`Ctrl+Shift+P` > "Preferences: Open Settings (JSON)")
- Installed VS Code extensions
- Git configuration for hooks