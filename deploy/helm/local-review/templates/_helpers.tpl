{{- define "review.image" -}}
{{- if .digest -}}{{ .repository }}@{{ .digest }}{{- else -}}{{ .repository }}:{{ .tag }}{{- end -}}
{{- end -}}
{{- define "review.labels" -}}
app.kubernetes.io/name: local-review
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}

{{- define "review.modelEnvironment" -}}
INFERENCE_TIMEOUT_SECONDS: {{ .Values.model.timeoutSeconds | toString | quote }}
MODEL_MAX_OUTPUT_TOKENS: {{ .Values.model.maxOutputTokens | toString | quote }}
{{- if .Values.model.expectedId }}
EXPECTED_MODEL_ID: {{ .Values.model.expectedId | quote }}
{{- end }}
{{- if .Values.model.expectedRevision }}
EXPECTED_MODEL_REVISION: {{ .Values.model.expectedRevision | quote }}
{{- end }}
{{- end -}}
