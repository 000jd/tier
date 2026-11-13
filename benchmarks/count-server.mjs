// Long-lived stdio token counter: reads one JSON array of strings per line,
// writes one JSON array of o200k_base token counts per line.
// Managed lazily by src/tier/tokens.py; one process serves the whole session.
import { createInterface } from 'node:readline';
import { encode } from 'gpt-tokenizer/encoding/o200k_base';

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', (line) => {
  if (!line.trim()) return;
  try {
    const arr = JSON.parse(line);
    if (!Array.isArray(arr)) throw new Error('expected an array');
    process.stdout.write(JSON.stringify(arr.map((x) => encode(String(x)).length)) + '\n');
  } catch (e) {
    process.stdout.write(JSON.stringify({ error: String(e && e.message ? e.message : e) }) + '\n');
  }
});
rl.on('close', () => process.exit(0));
