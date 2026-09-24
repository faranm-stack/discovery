import assert from 'node:assert/strict';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import Ajv from 'ajv';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const output = '/results/schemas';
await mkdir(output, { recursive: true });
const receipts = [];
const options = {
  strict: false,
  allErrors: true,
  validateFormats: true,
  removeAdditional: false,
  coerceTypes: false,
  useDefaults: false,
};
const validators = new Map([
  ['https://json-schema.org/draft/2020-12/schema', new Ajv2020(options)],
  ['http://json-schema.org/draft-07/schema#', new Ajv(options)],
]);
for (const validator of validators.values()) addFormats(validator);
const schemas = new Map();
const directories = [
  '/source/schemas',
  '/source/runtime/generated/retained/catalog-v1/runtime/generated/data/schemas',
];

function validatorFor(schemaId) {
  const schema = schemas.get(schemaId);
  assert.ok(schema, `Schema not registered: ${schemaId}`);
  const instance = validators.get(schema.$schema);
  assert.ok(instance, `Unsupported schema dialect: ${schema.$schema}`);
  return instance.getSchema(schemaId);
}

try {
  for (const directory of directories) {
    for (const file of await readdir(directory)) {
      if (!file.endsWith('.schema.json')) continue;
      const document = JSON.parse(await readFile(join(directory, file), 'utf8'));
      assert.equal(typeof document.$id, 'string', `Schema ID missing in ${file}`);
      if (schemas.has(document.$id)) {
        assert.deepEqual(document, schemas.get(document.$id), 'Conflicting schemas share an ID');
      } else {
        schemas.set(document.$id, document);
        const instance = validators.get(document.$schema);
        assert.ok(instance, `Unsupported schema dialect: ${document.$schema}`);
        instance.addSchema(document);
      }
    }
  }
  const inputValidator = validatorFor('https://flowblind.local/schemas/flowblind-catalog-research-input-v1.schema.json');
  assert.ok(inputValidator, 'The retained input schema must resolve');
  assert.equal(inputValidator({ researchGoal: 'Review synthetic data.' }), true);
  assert.equal(inputValidator({ researchGoal: 42 }), false);
  const cases = JSON.parse(await readFile('/results/public/schema-cases.json', 'utf8'));
  assert.ok(cases.length > 0, 'No emitted artifacts were available for schema validation');
  for (const item of cases) {
    const receipt = { id: item.id, schemaId: item.schemaId, path: item.path, expectedValid: item.expectedValid };
    try {
      const validator = validatorFor(item.schemaId);
      assert.ok(validator, `Schema not registered: ${item.schemaId}`);
      const document = JSON.parse(await readFile(item.path, 'utf8'));
      const valid = validator(document);
      receipt.observedValid = valid;
      receipt.errors = validator.errors ? structuredClone(validator.errors) : [];
      receipt.status = valid === item.expectedValid ? 'Passed' : 'Failed';
    } catch (error) {
      receipt.status = 'Blocked';
      receipt.error = error.message;
    }
    receipts.push(receipt);
    console.log(`${receipt.status}: ${receipt.id}`);
    if (receipt.status !== 'Passed') console.log(JSON.stringify(receipt.errors ?? receipt.error));
  }
} catch (error) {
  receipts.push({ id: 'schema-validation-setup', status: 'Blocked', error: error.message });
}

await writeFile(join(output, 'receipts.json'), `${JSON.stringify(receipts, null, 2)}\n`);
await writeFile(join(output, 'summary.json'), `${JSON.stringify({
  registeredSchemas: schemas.size,
  checks: receipts.length,
  passed: receipts.filter(item => item.status === 'Passed').length,
  failed: receipts.filter(item => item.status === 'Failed').length,
  blocked: receipts.filter(item => item.status === 'Blocked').length,
  sourceSha: '7bc7e6af8b0d29cdf2453025c6dcaed9b435c240',
}, null, 2)}\n`);
await writeFile(join(output, 'validator-package-lock.json'), await readFile('/review/package-lock.json'));
process.exitCode = receipts.some(item => item.status !== 'Passed') ? 1 : 0;
