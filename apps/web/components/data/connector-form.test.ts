import { describe, expect, it } from "vitest";
import { connectorFields, initialValues, splitValues } from "./connector-form";
import type { ConnectorKindSpec } from "@/lib/api/resources/data";

const pg: ConnectorKindSpec = {
  kind: "postgres",
  label: "PostgreSQL",
  secret_fields: ["password"],
  config_schema: {
    required: ["host", "database", "user"],
    properties: {
      host: { type: "string", title: "Host" },
      port: { type: "integer", default: 5432 },
      database: { type: "string" },
      user: { type: "string" },
      password: { type: "string", format: "password" },
      sslmode: { type: "string", enum: ["disable", "require"], default: "require" },
      read_only: { type: "boolean", default: true },
    },
  },
};

describe("connector form", () => {
  it("maps JSON-schema properties to inputs, with secrets as password fields", () => {
    const f = connectorFields(pg);
    const by = Object.fromEntries(f.map((x) => [x.name, x]));
    expect(by.password).toMatchObject({ input: "password", secret: true });
    expect(by.port).toMatchObject({ input: "number", defaultValue: "5432", label: "Port" });
    expect(by.sslmode).toMatchObject({ input: "select", options: ["disable", "require"] });
    expect(by.read_only).toMatchObject({ input: "checkbox", defaultValue: true });
    expect(by.host.required).toBe(true);
  });

  it("uses a textarea for long secrets like service-account JSON", () => {
    const f = connectorFields({ kind: "bigquery", label: "BigQuery", secret_fields: ["credentials_json"], config_schema: { properties: { credentials_json: { type: "string" } } } });
    expect(f[0]).toMatchObject({ input: "textarea", secret: true });
  });

  it("splits secrets from config, coerces numbers and reports missing required fields", () => {
    const fields = connectorFields(pg);
    const values = { ...initialValues(fields), host: "db.local", database: "wh", user: "", password: "s3cret" };
    const { config, secrets, missing } = splitValues(fields, values);
    expect(secrets).toEqual({ password: "s3cret" });
    expect(config).toMatchObject({ host: "db.local", port: 5432, database: "wh", sslmode: "require", read_only: true });
    expect(config).not.toHaveProperty("password");
    expect(missing).toEqual(["User"]);
  });
});
