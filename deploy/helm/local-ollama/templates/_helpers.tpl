{{- define "ollama.image" -}}
{{- if .Values.image.digest -}}{{ .Values.image.repository }}@{{ .Values.image.digest }}{{- else -}}{{ .Values.image.repository }}:{{ .Values.image.tag }}{{- end -}}
{{- end -}}
{{- define "ollama.labels" -}}
app.kubernetes.io/name: local-ollama
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}
{{- define "ollama.selector" -}}
app.kubernetes.io/name: local-ollama
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
{{- define "ollama.claim" -}}
{{- default (printf "%s-models" .Release.Name) .Values.persistence.existingClaim -}}
{{- end -}}
