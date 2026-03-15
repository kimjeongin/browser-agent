{{/*
Chart name, trimmed to 63 characters.
*/}}
{{- define "browser-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully-qualified release name.
*/}}
{{- define "browser-agent.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label value.
*/}}
{{- define "browser-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "browser-agent.labels" -}}
helm.sh/chart: {{ include "browser-agent.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: browser-agent
{{- end }}

{{/*
Selector labels scoped to a component.
Usage: {{ include "browser-agent.selectorLabels" (dict "component" "gateway" "context" .) }}
*/}}
{{- define "browser-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
{{- end }}

{{/*
Full label set (common + selector + component + version).
Usage: {{ include "browser-agent.componentLabels" (dict "component" "gateway" "context" .) }}
*/}}
{{- define "browser-agent.componentLabels" -}}
{{ include "browser-agent.labels" .context }}
{{ include "browser-agent.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/version: {{ .context.Chart.AppVersion | quote }}
{{- end }}

{{/*
Standard HostAlias block to reach the Minikube host (Ollama).
The host.docker.internal DNS name is not automatically set in Kubernetes pods;
we inject it via hostAliases using the configured ollamaHostIP.
*/}}
{{- define "browser-agent.ollamaHostAlias" -}}
hostAliases:
  - ip: {{ .Values.global.ollamaHostIP | quote }}
    hostnames:
      - host.docker.internal
{{- end }}

{{/*
Common environment variables injected into every app service container.
*/}}
{{- define "browser-agent.commonEnv" -}}
- name: OLLAMA_BASE_URL
  value: "http://host.docker.internal:11434"
- name: REDIS_URL
  value: {{ .Values.global.redisUrl | quote }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.global.otelEndpoint | quote }}
- name: OTEL_SERVICE_NAME
  value: {{ .component | quote }}
{{- end }}
