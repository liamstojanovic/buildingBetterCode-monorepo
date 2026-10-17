import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import taskSchema from "../../../core/schemas/Task.schema.json";
import resultSchema from "../../../core/schemas/RunResult.schema.json";

const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
export const validateTask = ajv.compile(taskSchema);
export const validateResult = ajv.compile(resultSchema);
