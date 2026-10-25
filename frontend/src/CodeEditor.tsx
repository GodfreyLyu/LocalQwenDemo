import CodeMirror from '@uiw/react-codemirror';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { json } from '@codemirror/lang-json';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import type { Extension } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import { useMemo } from 'react';

function syntaxFor(language: string): Extension {
  switch (language) {
    case 'python':
      return python();
    case 'javascript':
      return javascript({ jsx: true });
    case 'typescript':
      return javascript({ typescript: true, jsx: true });
    case 'json':
      return json();
    case 'html':
      return html();
    case 'css':
      return css();
    default:
      return [];
  }
}

export function CodeEditor({
  code,
  language,
  disabled,
  onChange,
}: {
  code: string;
  language: string;
  disabled: boolean;
  onChange: (code: string) => void;
}) {
  const extensions = useMemo(() => {
    let hint = language;
    if (hint === 'auto') {
      if (/^\s*(def |from \w+ import |import \w+$|class \w+.*:)/m.test(code))
        hint = 'python';
      else if (/\b(const |let |function |=>)/.test(code)) hint = 'javascript';
    }
    return [
      syntaxFor(hint),
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({
        'aria-label': 'Source code',
        'aria-multiline': 'true',
        role: 'textbox',
      }),
    ];
  }, [language, code]);
  return (
    <CodeMirror
      value={code}
      extensions={extensions}
      editable={!disabled}
      readOnly={disabled}
      onChange={onChange}
      height="100%"
      placeholder="Paste your code here…"
      basicSetup={{ foldGutter: false, highlightActiveLine: true }}
    />
  );
}
