import path from 'node:path';
import { pathToFileURL } from 'node:url';


const repo = process.argv[2];
if (!repo) {
  throw new Error('Missing pyramid-upload-to-r2 repository path');
}

let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
}
const paths = JSON.parse(input);
const moduleUrl = pathToFileURL(
  path.join(repo, 'scripts', 'local-bulk-upload.mjs'),
).href;
const { getCourseId, parseVideoPath } = await import(moduleUrl);

const results = {};
for (const relativePath of paths) {
  const parts = relativePath
    .split(/[\\/]/)
    .map((part) => part.trim())
    .filter(Boolean);
  const course = parts.length >= 3 ? parts.at(-3) : '';
  results[relativePath] = {
    identity: parseVideoPath(relativePath),
    courseKnown: getCourseId(course) != null,
  };
}

process.stdout.write(JSON.stringify(results));
