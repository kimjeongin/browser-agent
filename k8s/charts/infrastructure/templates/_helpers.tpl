{{/*
Expand the name of the chart.
*/}}
{{- define "infrastructure.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "infrastructure.fullname" -}}
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
Create chart label value (used in helm.sh/chart annotation).
*/}}
{{- define "infrastructure.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "infrastructure.labels" -}}
helm.sh/chart: {{ include "infrastructure.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: browser-agent
{{- end }}

{{/*
Selector labels for a given component name.
Usage: {{ include "infrastructure.selectorLabels" (dict "component" "postgresql" "context" .) }}
*/}}
{{- define "infrastructure.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
{{- end }}

{{/*
Component-scoped labels (common + selector).
Usage: {{ include "infrastructure.componentLabels" (dict "component" "postgresql" "context" .) }}
*/}}
{{- define "infrastructure.componentLabels" -}}
{{ include "infrastructure.labels" .context }}
{{ include "infrastructure.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/version: {{ .context.Chart.AppVersion | quote }}
{{- end }}
