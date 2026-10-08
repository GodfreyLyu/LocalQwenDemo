vi.mock('../CodeEditor', () => ({
  CodeEditor: ({
    code,
    disabled,
    onChange,
  }: {
    code: string;
    disabled: boolean;
    onChange: (value: string) => void;
  }) => (
    <textarea
      aria-label="Source code"
      value={code}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));
