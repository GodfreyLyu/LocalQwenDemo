import { LoaderCircle } from 'lucide-react';
import { CodeEditor } from './CodeEditor';

const languages = [
  ['auto', 'Auto detect'],
  ['python', 'Python'],
  ['javascript', 'JavaScript'],
  ['typescript', 'TypeScript'],
  ['json', 'JSON'],
  ['html', 'HTML'],
  ['css', 'CSS'],
  ['plain', 'Other / Plain text'],
];

interface SourcePanelProps {
  code: string;
  language: string;
  sourceMaxChars: number;
  blocked: boolean;
  runDisabled: boolean;
  reviewActive: boolean;
  onLanguageChange: (language: string) => void;
  onCodeChange: (code: string) => void;
  onLoadExample: () => void;
  onRunReview: () => void;
}

export function SourcePanel({
  code,
  language,
  sourceMaxChars,
  blocked,
  runDisabled,
  reviewActive,
  onLanguageChange,
  onCodeChange,
  onLoadExample,
  onRunReview,
}: SourcePanelProps) {
  return (
    <section className="panel source-panel">
      <div className="panel-title">
        <h2>Source code</h2>
        <select
          aria-label="Programming language"
          value={language}
          disabled={blocked}
          onChange={(event) => onLanguageChange(event.target.value)}
        >
          {languages.map(([value, label]) => (
            <option value={value} key={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="editor-container">
        <CodeEditor
          code={code}
          language={language}
          disabled={blocked}
          onChange={onCodeChange}
        />
      </div>
      <div className="editor-footer">
        <div className="editor-meta">
          <span className={code.length > sourceMaxChars ? 'over-limit' : ''}>
            {code.length.toLocaleString('en')} /{' '}
            {sourceMaxChars.toLocaleString('en')} characters
          </span>
          {!code && (
            <button
              className="text-button example-button"
              disabled={blocked}
              onClick={onLoadExample}
            >
              Load example
            </button>
          )}
        </div>
        <button
          className="primary run-button"
          onClick={onRunReview}
          disabled={runDisabled}
        >
          {reviewActive && (
            <LoaderCircle size={16} className="spin" aria-hidden="true" />
          )}
          Run review
        </button>
      </div>
    </section>
  );
}
