/** The key along the bottom of the machine: what the lit colours mean, and how to work it. */
const KEYS: [string, string][] = [
  ["sel", "selected"],
  ["up", "what it reads"],
  ["down", "what it feeds"],
  ["fail", "failed a gate"],
];

export function Legend() {
  return (
    <div className="mx-legend">
      {KEYS.map(([tone, word]) => (
        <span key={tone}>
          <i className={`mx-sw ${tone}`} aria-hidden="true" />
          {word}
        </span>
      ))}
      <span>drag to turn · click to select</span>
    </div>
  );
}
