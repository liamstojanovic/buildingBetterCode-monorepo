import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import taskSchema from "../../../core/schemas/Task.schema.json";
import resultSchema from "../../../core/schemas/RunResult.schema.json";
import advisorSchema from "../../../core/schemas/AdvisorReport.schema.json";
import type { AdvisorReport } from "./generated/AdvisorReport";

const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
export const validateTask = ajv.compile(taskSchema);
export const validateResult = ajv.compile(resultSchema);

// Advisor exports include defaults; normalize optional fields on a detached
// import so schema-valid reports that omit defaults render the same way.
const advisorAjv = new Ajv2020({ strict: false, useDefaults: true });
addFormats(advisorAjv);
export const validateAdvisor = advisorAjv.compile<AdvisorReport>(advisorSchema);
