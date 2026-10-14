// node khronos_validate.mjs file.glb  -> prints the Khronos glTF-Validator JSON report
import fs from 'node:fs';
import path from 'node:path';
import validator from 'gltf-validator';
const file = process.argv[2];
const report = await validator.validateBytes(new Uint8Array(fs.readFileSync(file)), {
  uri: path.basename(file), maxIssues: 50,
  ignoredIssues: ['UNUSED_OBJECT'],
  externalResourceFunction: (uri) => Promise.resolve(new Uint8Array(fs.readFileSync(path.resolve(path.dirname(file), decodeURIComponent(uri))))),
});
process.stdout.write(JSON.stringify(report));
