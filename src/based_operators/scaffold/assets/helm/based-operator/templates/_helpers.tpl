{{/*
Expand the chart name.
*/}}
{{- define "based-operator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
The fully qualified name of the release, used for every object of the chart.
*/}}
{{- define "based-operator.fullname" -}}
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
The chart version, as a label value.
*/}}
{{- define "based-operator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Labels shared by every object of the chart.
*/}}
{{- define "based-operator.labels" -}}
helm.sh/chart: {{ include "based-operator.chart" . }}
{{ include "based-operator.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
The labels the Deployment selects its pods by; they must stay stable.
*/}}
{{- define "based-operator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "based-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
The name of the service account the operator runs as.
*/}}
{{- define "based-operator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "based-operator.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
The name of the Secret that holds the webhook's TLS material.
*/}}
{{- define "based-operator.webhookSecretName" -}}
{{- printf "%s-webhook" (include "based-operator.fullname" .) }}
{{- end }}
