import { compile } from "json-schema-to-typescript";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
for (const name of ["Task", "RunEvent", "RunResult", "RunConfig", "AdvisorReport"]) {
  const schema = JSON.parse(
    await readFile(`${root}../core/schemas/${name}.schema.json`, "utf8"),
  );
  // A Python Any field has no JSON Schema type restriction. Preserve that
  // meaning instead of allowing the generator to infer an object from its title.
  if (name === "RunResult")
    schema.$defs.StepResult.properties.output.tsType = "unknown";
  const output = await compile(schema, name, {
    bannerComment:
      "/* Generated from core/schemas. Run bun run contracts; do not edit. */",
    unknownAny: true,
    style: { singleQuote: true, semi: true },
  });
  const path = `${root}src/api/generated/${name}.ts`;
  if (process.argv.includes("--check")) {
    if ((await readFile(path, "utf8")) !== output)
      throw new Error(`Contract drift: ${name}`);
  } else {
    await mkdir(`${root}src/api/generated`, { recursive: true });
    await writeFile(path, output);
  }
}
