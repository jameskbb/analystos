/** Turns a connector's JSON schema into form fields, and splits values into config vs secrets. Pure. */
import type { ConnectorKindSpec, JsonSchemaProp } from "@/lib/api/resources/data";

export interface ConnectorFieldSpec {
  name: string;
  label: string;
  input: "text" | "number" | "checkbox" | "password" | "textarea" | "select";
  required: boolean;
  secret: boolean;
  help?: string;
  options?: string[];
  defaultValue: string | boolean;
}

function baseType(p: JsonSchemaProp): string {
  if (Array.isArray(p.type)) return p.type.find((t) => t !== "null") ?? "string";
  if (p.type) return p.type;
  const alt = p.anyOf?.find((a) => a.type && a.type !== "null");
  return alt ? baseType(alt) : "string";
}

const LONG_TEXT = /(private_key|credentials_json|service_account|json|pem)/i;

export function connectorFields(spec: ConnectorKindSpec): ConnectorFieldSpec[] {
  const props = spec.config_schema?.properties ?? {};
  const required = new Set(spec.config_schema?.required ?? []);
  const secrets = new Set(spec.secret_fields);
  return Object.entries(props).map(([name, p]) => {
    const t = baseType(p);
    const secret = secrets.has(name) || p.writeOnly === true || p.format === "password";
    const enumOpts = (p.enum ?? p.anyOf?.find((a) => a.enum)?.enum)?.map(String);
    let input: ConnectorFieldSpec["input"] = "text";
    if (enumOpts?.length) input = "select";
    else if (t === "boolean") input = "checkbox";
    else if (t === "integer" || t === "number") input = "number";
    if (secret) input = LONG_TEXT.test(name) ? "textarea" : "password";
    else if (LONG_TEXT.test(name) && t === "string") input = "textarea";
    const def = p.default;
    return {
      name,
      label: p.title ?? name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      input,
      required: required.has(name),
      secret,
      help: p.description,
      options: enumOpts,
      defaultValue: input === "checkbox" ? def === true : def === undefined || def === null ? "" : String(def),
    };
  });
}

export type FormValues = Record<string, string | boolean>;

export function initialValues(fields: ConnectorFieldSpec[]): FormValues {
  return Object.fromEntries(fields.map((f) => [f.name, f.defaultValue]));
}

/** Splits form values into non-secret config and secrets; drops empty optional values and coerces numbers. */
export function splitValues(
  fields: ConnectorFieldSpec[],
  values: FormValues,
): { config: Record<string, unknown>; secrets: Record<string, unknown>; missing: string[] } {
  const config: Record<string, unknown> = {};
  const secrets: Record<string, unknown> = {};
  const missing: string[] = [];
  for (const f of fields) {
    const raw = values[f.name];
    if (f.input === "checkbox") {
      config[f.name] = raw === true;
      continue;
    }
    const s = typeof raw === "string" ? raw.trim() : "";
    if (!s) {
      if (f.required) missing.push(f.label);
      continue;
    }
    const v = f.input === "number" ? Number(s) : s;
    if (f.secret) secrets[f.name] = v;
    else config[f.name] = v;
  }
  return { config, secrets, missing };
}
