import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { createMonitoringApi, MonitoringApiError } from '../services/monitoringApi.js'
import {
  assessmentDates, blankActualRows, buildActualProgress, buildMonitoringRequest, buildMonitoringTrends,
  compareMonitoringReports, coordinatorExport, displayProgress,
  feedbackExport, filterTasks, formatDate, formatNumber, formatPercent,
  formatVariance, highRiskCount, importedPlanning, isCalendarDate, knownNumber, localToday,
  monitoringAlerts, reportCsv,
  monitoringAccessReadiness, monitoringFailureGuidance, monitoringFailureMetadata,
  plainText, readable, reportExport, reportScope,
} from '../services/monitoringView.js'
import { monitoringAccountKey, verifyAccountMonitoringSession } from '../services/accountMonitoring.js'
import './Monitoring.css'

// Each operation owns an abort controller and a sequence. Aborting alone is not
// sufficient when a transport has already resolved or does not honor the signal.
function useOperation(onAccessFailure) {
  const sequence = useRef(0)
  const controller = useRef(null)
  const [state, setState] = useState({ loading: false, value: null, error: '', failure: null })
  const cancel = useCallback(() => {
    sequence.current += 1
    controller.current?.abort()
    setState({ loading: false, value: null, error: '', failure: null })
  }, [])
  const run = useCallback(async operation => {
    const current = ++sequence.current
    controller.current?.abort()
    const next = new AbortController()
    controller.current = next
    setState({ loading: true, value: null, error: '', failure: null })
    try {
      const value = await operation(next.signal)
      if (sequence.current !== current || next.signal.aborted) return undefined
      setState({ loading: false, value, error: '', failure: null })
      return value
    } catch (error) {
      if (sequence.current !== current || next.signal.aborted) return undefined
      const message = error instanceof MonitoringApiError ? error.message : 'The operation could not be completed. No response was used.'
      const failure = monitoringFailureMetadata(error)
      setState({ loading: false, value: null, error: message, failure })
      if ([401, 403, 404].includes(failure.status)) onAccessFailure?.(failure)
      return undefined
    }
  }, [onAccessFailure])
  useEffect(() => () => {
    sequence.current += 1
    controller.current?.abort()
  }, [])
  return { ...state, run, cancel }
}

function Notice({ children, error = false }) {
  return children ? <div className={`monitoring-notice${error ? ' monitoring-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div> : null
}

function OperationNotice({ operation }) {
  if (!operation.error) return null
  const guidance = monitoringFailureGuidance(operation.failure)
  return <Notice error>{guidance && <strong>{guidance.title}</strong>}<p>{operation.error}</p>{guidance && <p>{guidance.message}</p>}</Notice>
}

function TextList({ items, empty = 'None reported.' }) {
  return Array.isArray(items) && items.length ? <ul className="monitoring-list">{items.map((item, index) => <li key={index}>{plainText(item)}</li>)}</ul> : <p className="monitoring-muted">{empty}</p>
}

function Progress({ value, label }) {
  const display = displayProgress(value)
  return <div className="monitoring-progress"><span>{label}</span><strong>{formatPercent(value)}</strong>{display !== null && <progress value={display} max="100" aria-label={label} />}</div>
}

function downloadText(text, filename, type) {
  const blob = new Blob([text], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename.replace(/[^A-Za-z0-9_.-]/g, '_')
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function downloadJson(value, filename) {
  downloadText(JSON.stringify(value, null, 2), filename, 'application/json')
}

function TaskLinks({ ids, tasks, onTask }) {
  return ids?.length > 0 ? <p>Linked tasks: {ids.map(id => tasks.some(task => task.task_id === id)
    ? <a key={id} href={`#monitoring-task-${encodeURIComponent(id)}`} onClick={event => { event.preventDefault(); onTask(id) }}>{id} </a>
    : <span key={id}>{id} (not in this report) </span>)}</p> : null
}

function ReportComparison({ report, reports, projectId, onTask }) {
  const [previousId, setPreviousId] = useState('')
  const { candidates, omitted } = useMemo(() => {
    const candidates = []
    let omitted = 0
    for (const item of reports) {
      try {
        compareMonitoringReports(item, null, projectId)
        if (item.report_id !== report.report_id) candidates.push(item)
      } catch { omitted += 1 }
    }
    return { candidates, omitted }
  }, [reports, projectId, report.report_id])
  const previous = candidates.find(item => item.report_id === previousId)
  const comparison = useMemo(() => {
    if (!previous) return null
    try { return compareMonitoringReports(report, previous, projectId) }
    catch { return null }
  }, [report, previous, projectId])
  const labels = [['actual_progress', 'Actual progress', true], ['expected_progress', 'Expected progress', true], ['progress_variance', 'Progress variance', true], ['delayed_tasks', 'Confirmed delayed tasks'], ['blocked_tasks', 'Blocked tasks'], ['overdue_tasks', 'Overdue tasks'], ['high_risks', 'HIGH / CRITICAL risks']]
  return <section aria-labelledby="monitoring-comparison-title">
    <h3 id="monitoring-comparison-title">Compare saved snapshots</h3>
    <div className="monitoring-no-print"><label>Compare this report with a saved report<select value={previous?.report_id || ''} onChange={event => setPreviousId(event.target.value)}><option value="">Choose a saved snapshot</option>{candidates.map(item => <option key={item.report_id} value={item.report_id}>{formatDate(item.as_of)} · {item.report_id}</option>)}</select></label>
      {!candidates.length && <p className="monitoring-muted">No other authorized saved reports are available in the loaded history.</p>}
    </div>
    {omitted > 0 && <Notice>Some saved snapshots were omitted from comparison because their metadata could not be validated for this authorized project. The current report is unchanged.</Notice>}
    {previous && !comparison && <Notice error>Comparison could not be validated. No comparison was used. The current report remains available for review.</Notice>}
    <p className="monitoring-muted">Current minus selected snapshot. Progress differences are percentage points; count differences are counts. Unknown inputs remain unknown. Coverage and baseline changes can make snapshots non-comparable.</p>
    {comparison && <>
      <p>Selected snapshot: {previous.report_id} · as of {formatDate(previous.as_of)} · generated {plainText(previous.generated_at)}</p>
      <Notice>{comparison.comparable ? 'Some changes are available for review. Unknown deltas are not established; review every baseline and coverage caution below.' : 'These snapshots are not directly comparable. Do not interpret their differences as measured project improvement or regression.'}<TextList items={comparison.reasons} empty="No additional comparison cautions returned." /></Notice>
      <dl className="monitoring-comparison-deltas">{labels.map(([key, label, progress]) => <div key={key}><dt>{label}</dt><dd>{progress ? formatVariance(comparison.deltas[key]) : knownNumber(comparison.deltas[key]) ? `${comparison.deltas[key] > 0 ? '+' : ''}${formatNumber(comparison.deltas[key])}` : 'Unknown'}</dd></div>)}</dl>
      <h4>Task changes</h4>
      {comparison.task_changes.length ? <ul className="monitoring-list">{comparison.task_changes.map(change => <li key={change.task_id}><strong>{change.title || change.task_id}</strong><p>{readable(change.from_status)} → {readable(change.to_status)} · progress change {formatVariance(change.progress_delta)} · {readable(change.change)}</p><TaskLinks ids={[change.task_id]} tasks={report.tasks} onTask={onTask} /></li>)}</ul> : <p>No task changes returned. Missing observations are not proof of unchanged execution.</p>}
      <h4>Risk changes</h4>
      {!comparison.risk_changes.assessment_available && <Notice>Risk assessments are not available for both snapshots. Risk changes cannot establish resolution or zero risk.</Notice>}
      <h4>Newly returned</h4><TextList items={comparison.risk_changes.new.map(risk => typeof risk === 'string' ? risk : `${risk.risk_id}: ${risk.description || 'No description supplied'}`)} empty="No newly returned risks identified." />
      <h4>No longer returned</h4><TextList items={comparison.risk_changes.no_longer_returned.map(risk => typeof risk === 'string' ? risk : `${risk.risk_id}: ${risk.description || 'No description supplied'}`)} empty="No previously returned risks absent from this snapshot." /><p className="monitoring-muted">Absence from a response does not mean resolved. Only an explicit returned status change can establish a reported status change.</p>
      <h4>Explicit status changes</h4><TextList items={comparison.risk_changes.status_changed.map(risk => `${risk.risk_id} · ${risk.description}: ${readable(risk.from_status)} → ${readable(risk.to_status)}`)} empty="No explicit risk status changes returned." />
    </>}
  </section>
}

function SavedTrends({ reports, projectId }) {
  const trends = useMemo(() => buildMonitoringTrends(reports, projectId), [reports, projectId])
  if (!trends.points.length) return null
  return <section className="monitoring-trends" aria-labelledby="monitoring-trends-title">
    <h3 id="monitoring-trends-title">Saved as-of snapshots</h3>
    <p id="monitoring-trends-help" className="monitoring-muted">Loaded history is limited to 20 saved reports, not all-time history. Bars are independent snapshots, not a continuous forecast; different baselines are never connected. Missing bars mean unknown, not 0%. Actual averages may have different observation coverage.</p>
    <TextList items={trends.limitations} empty="Review each snapshot’s coverage and baseline before comparing." />
    <div className="monitoring-chart" role="region" aria-label="Actual and expected progress by saved snapshot" aria-describedby="monitoring-trends-help" tabIndex="0">
      <ResponsiveContainer width="100%" height={300} minWidth={0}>
        <BarChart data={trends.points} margin={{ top: 12, right: 12, bottom: 12, left: 0 }} accessibilityLayer>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 12 }} minTickGap={24} />
          <YAxis unit="%" width={48} domain={[0, 100]} tick={{ fontSize: 12 }} />
          <Tooltip formatter={value => formatPercent(value)} labelFormatter={(label, payload) => `${label}${payload?.[0]?.payload?.baseline_changed ? ' · baseline changed' : ''}`} />
          <Legend />
          <Bar dataKey="actual_progress" name="Actual · observed" fill="#2b4ea8" isAnimationActive={false} />
          <Bar dataKey="expected_progress" name="Expected · estimate / target" fill="#b5472b" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
    <div className="monitoring-table-scroll" tabIndex="0" role="region" aria-label="Saved snapshot data, horizontally scrollable"><table><caption>Accessible snapshot data · Unknown values are not zero · Baseline changes are flagged</caption><thead><tr><th scope="col">As of / report</th><th scope="col">Actual</th><th scope="col">Expected</th><th scope="col">Confirmed delays</th><th scope="col">HIGH / CRITICAL risks</th><th scope="col">Coverage / baseline</th></tr></thead><tbody>{trends.points.map(point => <tr key={point.report_id}><th scope="row">{formatDate(point.as_of)}<small>{point.report_id}</small><small>Generated: {plainText(point.generated_at)}</small></th><td>{formatPercent(point.actual_progress)}</td><td>{formatPercent(point.expected_progress)}</td><td>{formatNumber(point.delayed_tasks)}</td><td>{formatNumber(point.high_risks)}</td><td>{point.coverage_label}<small>{point.baseline_changed ? 'Baseline changed — review before comparing' : 'No baseline change identified; not proof of identical scope'}</small></td></tr>)}</tbody></table></div>
  </section>
}

function ReportAlerts({ report, projectId, onTask }) {
  const alerts = useMemo(() => {
    try { return monitoringAlerts(report, projectId) }
    catch { return null }
  }, [report, projectId])
  return <section aria-labelledby="monitoring-alerts-title"><h3 id="monitoring-alerts-title">Evidence-linked review alerts</h3><p className="monitoring-muted">In-app prompts for human review only. No email, notification delivery, or agent message is sent.</p>
    {alerts === null ? <Notice error>Review alerts could not be validated. No alerts were derived; review the report’s recorded evidence and limitations.</Notice> : alerts.length ? <ul className="monitoring-alerts">{alerts.map(alert => <li key={alert.id}><strong><span className="monitoring-tag">{readable(alert.severity)}</span> {alert.title}</strong><p>{alert.message}</p><TaskLinks ids={alert.task_ids} tasks={report.tasks} onTask={onTask} />{alert.source_refs.length > 0 && <><h4>Source references</h4><TextList items={alert.source_refs} /></>}{alert.recommended_action && <p>For review: {alert.recommended_action}</p>}</li>)}</ul> : <p>No review alerts derived from this report. This does not establish complete coverage or zero risk.</p>}
  </section>
}

function Coverage({ report }) {
  const coverage = report.coverage
  if (!coverage) return <Notice>Unknown legacy scope: this saved report has no coverage metadata. Its status and risk count cannot establish complete project coverage.</Notice>
  return <section aria-labelledby="monitoring-coverage-title">
    <div className="monitoring-section-heading"><h3 id="monitoring-coverage-title">Input coverage</h3><span>Scope, not agent uptime</span></div>
    <div className="monitoring-coverage-grid">
      <article><h4>Planning</h4><strong>{readable(coverage.planning_status)}</strong><p>Freshness: {readable(coverage.planning_freshness)}</p><p>Updated: {formatDate(coverage.planning_updated_on)}</p><p>Fetched / observed: {formatDate(coverage.planning_observed_on)}</p></article>
      <article><h4>Actual observations</h4><strong>{readable(coverage.actual_status)}</strong><p>{formatNumber(coverage.fresh_actual_tasks)} fresh / {formatNumber(coverage.total_tasks)} tasks</p><p>{formatNumber(coverage.missing_actual_tasks)} missing · {formatNumber(coverage.stale_actual_tasks)} stale</p><p>Updated: {formatDate(coverage.actual_updated_on)}</p><p>Observed: {formatDate(coverage.actual_observed_on)}</p></article>
      <article><h4>Risk source</h4><strong>{readable(coverage.risk_status)}</strong><p>Source observed: {formatDate(coverage.risk_observed_on)}</p><p>A configured source is not proof of a successful fetch.</p></article>
      <article><h4>Milestones</h4><strong>{readable(coverage.milestones_status)}</strong><p>Only supplied milestone definitions can be assessed.</p></article>
    </div>
    <p className="monitoring-muted">Expected method: {readable(coverage.expected_progress_method)}. Actual method: {readable(coverage.actual_progress_method)}. Estimates and observation averages are not verified project completion.</p>
  </section>
}

function TaskAssessmentRow({ task, printable = false }) {
  const dates = assessmentDates(task)
  return <tr id={printable ? undefined : `monitoring-task-${encodeURIComponent(task.task_id)}`} tabIndex={printable ? undefined : -1}>
    <th scope="row"><strong>{task.title}</strong><small>{task.task_id}</small>{task.is_critical === true && <small>Critical task</small>}</th>
    <td><span className="monitoring-tag">{readable(task.status)}</span>{task.delayed && <small>Delayed</small>}{task.dependency_delayed === true && <small>{task.delayed === true ? 'Upstream dependency delayed' : 'Upstream dependency delayed; this task’s delay is not established'}</small>}{task.planned_deadline_passed && <small>Planned deadline passed; actual outcome unknown</small>}</td>
    <td>{formatPercent(task.expected_progress)}</td>
    <td>{formatPercent(task.actual_progress)}{knownNumber(task.last_observed_progress) && <small>Last observation: {formatPercent(task.last_observed_progress)} (not current)</small>}</td>
    <td>{formatVariance(task.variance)}</td>
    <td><small>Planned: {formatDate(dates.plannedStart)} → {formatDate(dates.dueDate)}</small><small>Actual: {formatDate(dates.actualStart)} → {formatDate(dates.actualEnd)}</small><small>Freshness date: {formatDate(dates.observedOn)} (source update or recorded observation)</small><small>Late start: {formatNumber(task.start_delay_days)} days</small><small>Overdue: {formatNumber(task.overdue_days)} days · completed late: {formatNumber(task.completed_late_days)} days</small></td>
  </tr>
}

function ReportView({ report, projectId, saved, reports }) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [exportError, setExportError] = useState('')
  const metrics = report.metrics
  const tasks = filterTasks(report.tasks, search, status)
  function exportFile(kind) {
    try {
      if (kind === 'csv') {
        downloadText(reportCsv(report, projectId), `monitoring-tasks-${report.report_id}.csv`, 'text/csv;charset=utf-8')
      } else {
        const value = kind === 'report' ? reportExport(report, projectId) : kind === 'feedback' ? feedbackExport(report, projectId) : coordinatorExport(report, projectId)
        downloadJson(value, `monitoring-${kind}-${report.report_id}.json`)
      }
      setExportError('')
    } catch (error) { setExportError(error.message) }
  }
  function showTask(taskId) {
    setStatus('all')
    setSearch('')
    requestAnimationFrame(() => {
      const target = document.getElementById(`monitoring-task-${encodeURIComponent(taskId)}`)
      target?.scrollIntoView({ block: 'center' })
      target?.focus({ preventScroll: true })
    })
  }
  return <section className="monitoring-panel monitoring-report" aria-labelledby="monitoring-report-title">
    <div className="monitoring-section-heading">
      <div><p className="monitoring-eyebrow">{saved ? 'Saved report' : 'Generated report · not saved'}</p><h2 id="monitoring-report-title">{readable(report.status)}</h2><p>{reportScope(report)}</p></div>
      <div className="monitoring-report-meta"><p>Project: {report.project_id}</p><p>As of {formatDate(report.as_of)}</p><p>Report: {report.report_id}</p><p>Generated: {plainText(report.generated_at)}</p></div>
    </div>
    <Notice><strong>Human review required.</strong> Review source coverage, warnings, and evidence before acting. This is not a verified completion forecast or an automatic Planning update.</Notice>
    <p className="monitoring-summary">{report.summary || 'No narrative summary supplied.'}</p>
    <Coverage report={report} />
    <div className="monitoring-metrics">
      <article><h4>Reported actual progress</h4><Progress value={metrics.actual_progress} label="Actual · available observations" /><p>Missing and stale observations are not zero. {metrics.actual_progress_is_lower_bound === true ? 'The server labels this metric a lower bound.' : 'Read the coverage and averaging method.'}</p></article>
      <article><h4>Expected progress</h4><Progress value={metrics.expected_progress} label="Expected · estimate / supplied target" /><p>Calendar estimates are not observed execution.</p></article>
      <article><h4>Progress variance</h4><strong className="monitoring-metric-value">{formatVariance(metrics.progress_variance)}</strong><p>Actual minus expected, in percentage points.</p></article>
      <article><h4>HIGH / CRITICAL risks</h4><strong className="monitoring-metric-value">{formatNumber(highRiskCount(report))}</strong><p>{report.coverage?.risk_status === 'available' ? 'High-severity risks only, not all risks.' : 'Not checked with available risk coverage; not zero.'}</p></article>
    </div>
    <dl className="monitoring-counts">{[['Tasks', metrics.total_tasks], ['Completed', metrics.completed_tasks], ['Missing actuals', metrics.missing_actual_tasks], ['Stale actuals', metrics.stale_actual_tasks], ['Blocked', metrics.blocked_tasks], ['Delayed', metrics.delayed_tasks], ['Overdue', metrics.overdue_tasks]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{formatNumber(value)}</dd></div>)}</dl>
    <ReportComparison report={report} reports={reports} projectId={projectId} onTask={showTask} />
    <ReportAlerts report={report} projectId={projectId} onTask={showTask} />
    <section className="monitoring-cautions" aria-labelledby="monitoring-cautions-title">
      <h3 id="monitoring-cautions-title">Warnings & limitations</h3>
      {report.warnings.length ? <ul className="monitoring-list">{report.warnings.map((warning, index) => <li key={index}><strong>{readable(warning.severity)} · {plainText(warning.code)}</strong><p>{plainText(warning.message)}</p>{warning.task_ids?.length > 0 && <p>Tasks: {warning.task_ids.join(', ')}</p>}{warning.source_refs?.length > 0 && <p>Sources: {warning.source_refs.map(plainText).join('; ')}</p>}</li>)}</ul> : <p>No warnings returned. This does not establish complete coverage.</p>}
      <h4>Limitations</h4><TextList items={report.limitations} empty="No limitations returned; still review the recorded input coverage." />
    </section>
    <section aria-labelledby="monitoring-tasks-title">
      <div className="monitoring-section-heading"><h3 id="monitoring-tasks-title">Task assessments</h3><span className="monitoring-no-print">{tasks.length} of {report.tasks.length} shown</span></div>
      <div className="monitoring-filters"><label>Search title or ID<input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label><label>Assessment filter<select value={status} onChange={event => setStatus(event.target.value)}><option value="all">All statuses</option><option value="missing">Missing observations</option><option value="stale">Stale observations</option><option value="blocked">Blocked</option><option value="delayed">Confirmed task delay</option><option value="dependency">Upstream dependency delayed</option><option value="completed">Completed</option><option value="in_progress">In progress</option><option value="not_started">Not started</option></select></label></div>
      <div className="monitoring-table-scroll monitoring-no-print" tabIndex="0" role="region" aria-label="Task assessments, horizontally scrollable"><table><caption>Only report observations establish actual status. Dates absent from this report remain unknown.</caption><thead><tr><th scope="col">Task</th><th scope="col">Assessment</th><th scope="col">Expected estimate / target</th><th scope="col">Actual</th><th scope="col">Variance</th><th scope="col">Evidence & dates</th></tr></thead><tbody>{tasks.map(task => <TaskAssessmentRow key={task.task_id} task={task} />)}</tbody></table>{!tasks.length && <p className="monitoring-empty">No tasks match these filters.</p>}</div>
      <div className="monitoring-print-only"><table><caption>All {report.tasks.length} task assessments · Screen filters do not omit printable evidence</caption><thead><tr><th scope="col">Task</th><th scope="col">Assessment</th><th scope="col">Expected</th><th scope="col">Actual</th><th scope="col">Variance</th><th scope="col">Evidence & dates</th></tr></thead><tbody>{report.tasks.map(task => <TaskAssessmentRow key={task.task_id} task={task} printable />)}</tbody></table></div>
    </section>
    <section aria-labelledby="monitoring-risks-title"><div className="monitoring-section-heading"><h3 id="monitoring-risks-title">Risks & evidence</h3><span>Confidence is not a probability</span></div><p>{report.risk_summary || 'No risk summary supplied.'}</p>
      <div className="monitoring-risk-list">{(report.risks || []).map(risk => <details key={risk.risk_id}><summary><span className="monitoring-tag">{readable(risk.level)}</span> {risk.description || risk.risk} <small>{readable(risk.status)}</small></summary><p>Source: {plainText(risk.source)}</p><p>Evidence confidence: {knownNumber(risk.confidence) ? formatPercent(risk.confidence * 100) : 'Unknown'} (not likelihood or project completion)</p><p>Affected area: {plainText(risk.affected_area)}</p><h4>Evidence</h4><TextList items={Array.isArray(risk.evidence) ? risk.evidence : risk.evidence ? [risk.evidence] : []} empty="No evidence supplied for this risk." /><p>Recommended action: {plainText(risk.recommended_action)}</p>{risk.task_ids?.length > 0 && <TaskLinks ids={risk.task_ids} tasks={report.tasks} onTask={showTask} />}</details>)}</div>
      {!report.risks?.length && <p className="monitoring-empty">{report.coverage?.risk_status === 'available' ? 'No risks returned by the available risk source.' : 'No usable risk assessment supplied. This does not mean no risks exist.'}</p>}
      <details><summary>Source evidence and references</summary><h4>Retrieved evidence</h4><TextList items={report.retrieved_evidence} /><h4>Risk evidence</h4><TextList items={report.risk_evidence_used} /><h4>Risk knowledge sources</h4><TextList items={report.risk_knowledge_sources} /><h4>Summary facts</h4><TextList items={report.facts} /></details>
    </section>
    <section><h3>Milestones</h3>{report.coverage?.milestones_status !== 'available' && <p className="monitoring-muted">Milestone coverage: {report.coverage ? readable(report.coverage.milestones_status) : 'Unknown legacy scope'}.</p>}{report.milestones?.map(milestone => <div className="monitoring-milestone" key={milestone.milestone_id}><strong>{milestone.title}</strong><span>{formatPercent(milestone.progress)} · {knownNumber(milestone.progress) ? readable(milestone.status) : 'Actual outcome unknown'}</span></div>)}{!report.milestones?.length && <p className="monitoring-muted">No milestone definitions were assessed.</p>}</section>
    <section><h3>Suggested actions</h3><TextList items={report.suggested_actions} /></section>
    <details><summary>Input provenance & passive Planning feedback</summary><p>Feedback is an export artifact only. It has not been dispatched. Planning currently does not support automatic feedback handling; a human must review and coordinate any schedule change.</p><h4>Planning feedback</h4><TextList items={report.planning_feedback} /><div className="monitoring-source-context"><h4>Planning context captured with this report</h4><pre>{plainText(report.planning_context)}</pre><h4>Actual-observation provenance</h4><pre>{plainText(report.actual_context)}</pre></div></details>
    <div className="monitoring-actions"><button type="button" onClick={() => exportFile('report')}>Export report JSON</button><button type="button" onClick={() => exportFile('feedback')}>Export Planning feedback JSON</button><button type="button" onClick={() => exportFile('coordinator')}>Export Coordinator message JSON</button><button type="button" onClick={() => exportFile('csv')}>Export task CSV</button><button type="button" onClick={() => window.print()}>Print / Save as PDF</button></div>
    <p className="monitoring-muted monitoring-no-print">Print opens your browser’s print dialog; choose “Save as PDF” yourself. No PDF is downloaded automatically.</p>
    <p className="monitoring-muted monitoring-no-print">Exports preserve report/project/correlation metadata and contain no access-token field. No messages are posted to agents. Files may contain private project data; share carefully.</p><Notice error>{exportError}</Notice>
  </section>
}

const actualFields = ['project_id', 'tasks', 'updated_on']
const actualTaskFields = ['task_id', 'status', 'progress_percentage', 'actual_start', 'actual_end', 'updated_on']
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value)

function validateObservationActual(actual, projectId, observedOn, asOf) {
  if (!isCalendarDate(observedOn) || observedOn > asOf || observedOn > localToday()) throw new Error('Enter an explicit observation date no later than the report date or today.')
  if (!isRecord(actual) || actual.project_id !== projectId || !Array.isArray(actual.tasks) || !actual.tasks.length || Object.keys(actual).some(key => !actualFields.includes(key))) throw new Error('ActualProgress JSON must contain the authorized project_id and real, explicit task observations. Only project_id, tasks, and optional updated_on are accepted.')
  if (actual.tasks.some(task => !isRecord(task) || Object.keys(task).some(key => !actualTaskFields.includes(key)) || !knownNumber(task.progress_percentage))) throw new Error('Each task needs an explicit numeric progress_percentage and supported observation fields; no status or progress is inferred.')
  // Recognize the documented alias only in the validation copy, retaining the
  // original payload and genuine update dates for server canonicalization.
  buildActualProgress(actual.tasks.map(task => ({ ...task, status: task.status === 'PLANNED' ? 'NOT_STARTED' : task.status, included: true })), projectId, observedOn, asOf)
  for (const item of [actual, ...actual.tasks]) {
    if (item.updated_on != null && (!isCalendarDate(item.updated_on) || item.updated_on > observedOn)) throw new Error('Genuine updated_on dates must be valid and no later than the observation date. Do not replace source dates with submission dates.')
  }
  return actual
}

function receiptExport(receipt, projectId) {
  if (receipt.project_id !== projectId) throw new Error('Receipt does not belong to the selected authorized project.')
  const fields = ['observation_id', 'request_id', 'project_id', 'source', 'observed_on', 'accepted_at', 'submitted_by', 'persisted', 'limitations']
  return {
    ...Object.fromEntries(fields.filter(field => Object.hasOwn(receipt, field)).map(field => [field, receipt[field]])),
    actual_progress: {
      ...Object.fromEntries(actualFields.filter(field => field !== 'tasks' && Object.hasOwn(receipt.actual_progress, field)).map(field => [field, receipt.actual_progress[field]])),
      tasks: receipt.actual_progress.tasks.map(task => Object.fromEntries(actualTaskFields.filter(field => Object.hasOwn(task, field)).map(field => [field, task[field]]))),
    },
  }
}

function ObservationIntake({ api, source, projectId, baseline, planningText, receivedOn, asOf, canRead, canGenerate, capabilities, onUnauthorized, onAdopt, onInvalidate, selectedReceipt }) {
  const submitOperation = useOperation(onUnauthorized)
  const historyOperation = useOperation(onUnauthorized)
  const detailOperation = useOperation(onUnauthorized)
  const [text, setText] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [date, setDate] = useState('')
  const [save, setSave] = useState(false)
  const [receipt, setReceipt] = useState(null)
  const [loadedByGet, setLoadedByGet] = useState(false)
  const [reviewId, setReviewId] = useState('')
  const [error, setError] = useState('')
  const storageConfigured = capabilities?.observations_storage_configured === true
  const busy = submitOperation.loading || detailOperation.loading

  function clearSelection() {
    submitOperation.cancel()
    detailOperation.cancel()
    setReceipt(null)
    setLoadedByGet(false)
    setReviewId('')
    setError('')
    onAdopt(null)
    onInvalidate()
  }
  function edit(setter, value) { clearSelection(); historyOperation.cancel(); setter(value) }
  async function submit() {
    clearSelection()
    let body
    try {
      if (!canGenerate) throw new Error('Observation validation requires monitoring:generate permission.')
      if (!baseline || baseline.project_id !== projectId) throw new Error('Load a real baseline first. The server validates the current baseline and task IDs again.')
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(sourceId.trim())) throw new Error('Enter a source identifier of 1–100 letters, digits, dots, underscores, or hyphens, starting with a letter or digit. This is a caller claim, not cryptographic agent proof.')
      if (typeof crypto.randomUUID !== 'function') throw new Error('A secure browser context is required for request IDs.')
      let actual
      try { actual = JSON.parse(text) } catch { throw new Error('Paste a real ActualProgress object as valid JSON.') }
      validateObservationActual(actual, projectId, date, asOf)
      if (actual.tasks.some(task => !baseline.tasks.some(planned => planned.task_id === task.task_id))) throw new Error('The observations reference tasks outside the selected baseline.')
      body = { request_id: crypto.randomUUID(), source: sourceId.trim(), observed_on: date, actual_progress: actual, save_observation: save && storageConfigured }
      if (source === 'import') {
        const { message } = importedPlanning(planningText, projectId, receivedOn)
        if (receivedOn > asOf || receivedOn > localToday()) throw new Error('Planning receipt date must be no later than the report date or today.')
        body.planning_message = message
        body.received_on = receivedOn
      }
    } catch (failure) { setError(failure.message); return }
    const result = await submitOperation.run(signal => api.submitObservation(projectId, body, signal))
    if (result) {
      setReceipt(result)
      if (result.persisted && canRead && storageConfigured) historyOperation.run(signal => api.observationHistory(projectId, signal))
    }
  }
  async function review(id) {
    clearSelection()
    if (!id || !canRead || !storageConfigured) return
    setReviewId(id)
    const result = await detailOperation.run(signal => api.observation(projectId, id, signal))
    if (result) { setReceipt(result); setLoadedByGet(true) }
  }
  function adopt() {
    try {
      if (!canGenerate || receipt?.persisted && (!canRead || !loadedByGet)) throw new Error('Stored receipts require read and generate permission and a protected GET before adoption.')
      if (!receipt || receipt.project_id !== projectId) throw new Error('Review a receipt for this authorized project first.')
      validateObservationActual(receipt.actual_progress, projectId, receipt.observed_on, asOf)
      if (!baseline || receipt.actual_progress.tasks.some(task => !baseline.tasks.some(planned => planned.task_id === task.task_id))) throw new Error('Receipt task IDs do not match this displayed baseline. The server must validate the current baseline before Risk is called.')
      onInvalidate()
      onAdopt(receipt)
      setError('')
    } catch (failure) { setError(failure.message) }
  }
  function exportReceipt() {
    try { downloadJson(receiptExport(receipt, projectId), `monitoring-observation-${receipt.observation_id}.json`); setError('') }
    catch (failure) { setError(failure.message) }
  }
  return <details className="monitoring-observation-intake"><summary>Advanced: validate real observation JSON & review stored receipts</summary>
    <p>Validation does not authenticate the claimed source or prove future task membership. Live intake uses the backend’s current Planning baseline; imported intake supplies the real Planning envelope for ID validation. This does not send messages to other agents.</p>
    <label>Real ActualProgress JSON<textarea rows="8" value={text} spellCheck="false" onChange={event => edit(setText, event.target.value)} aria-describedby="monitoring-observation-json-help" /></label>
    <p id="monitoring-observation-json-help" className="monitoring-muted">Paste only your real project_id, tasks, and optional updated_on. Each task needs task_id, status, numeric progress_percentage, and any genuine actual_start, actual_end, or updated_on. Dates and values are never filled in for you.</p>
    <div className="monitoring-form-grid"><label>Caller-claimed source identifier<input type="text" value={sourceId} maxLength="100" onChange={event => edit(setSourceId, event.target.value)} /></label><label>Explicit observation date<input type="date" value={date} max={asOf < localToday() ? asOf : localToday()} onChange={event => edit(setDate, event.target.value)} /></label></div>
    <div className="monitoring-options"><label><input type="checkbox" checked={save && storageConfigured} disabled={!storageConfigured || !canGenerate} onChange={event => edit(setSave, event.target.checked)} /> Save observation receipt (explicit opt-in)</label></div>
    <p className="monitoring-muted">{storageConfigured ? 'Observation storage is configured, not table-verified. The operator must apply the manual observation-table migration; configuration alone does not prove the table exists or a save will succeed.' : 'Observation storage is not confirmed configured. Validation-only intake is available; receipt saving and stored history are disabled.'}</p>
    <div className="monitoring-actions"><button type="button" disabled={!canGenerate || !baseline || !text.trim() || !sourceId.trim() || !date || busy} onClick={submit}>{submitOperation.loading ? 'Validating observations…' : save && storageConfigured ? 'Validate & save observation receipt' : 'Validate only · do not save'}</button>{busy && <button type="button" onClick={() => { clearSelection(); historyOperation.cancel(); setError('Request cancelled locally. A requested save may still finish on the server; check stored history before retrying.') }}>Cancel observation request</button>}</div>
    <OperationNotice operation={submitOperation} />
    <h4>Stored receipt history · explicit review</h4>
    <p className="monitoring-muted">No latest receipt is automatically selected. A saved receipt is provenance, not proof that its tasks exist in a later baseline. Loaded history may be limited to 20 records, not all-time history.</p>
    <div className="monitoring-actions"><button type="button" disabled={!canRead || !storageConfigured || historyOperation.loading} onClick={() => historyOperation.run(signal => api.observationHistory(projectId, signal))}>{historyOperation.loading ? 'Loading receipt history…' : 'Refresh stored receipt history'}</button></div>
    {!canRead && <p>This session has no monitoring:read permission. Stored receipts cannot be read or selected.</p>}
    {canRead && storageConfigured && <label>Stored receipt to review<select value={reviewId} disabled={historyOperation.loading || busy} onChange={event => review(event.target.value)}><option value="">Choose a stored receipt · GET for review</option>{historyOperation.value?.map(item => <option key={item.observation_id} value={item.observation_id}>{formatDate(item.observed_on)} · {item.source} · {item.observation_id}</option>)}</select></label>}
    {historyOperation.value?.length === 0 && <p>No stored receipts returned for this authorized project.</p>}
    <OperationNotice operation={historyOperation} /><OperationNotice operation={detailOperation} />
    {detailOperation.loading && <Notice>Loading the selected stored receipt for review…</Notice>}
    {receipt && <article className="monitoring-receipt" aria-label="Validated observation receipt">
      <h4>{receipt.persisted ? 'Persisted observation receipt' : 'Validation-only receipt · not persisted'}</h4>
      <dl><div><dt>Observation / request</dt><dd>{receipt.observation_id} / {receipt.request_id}</dd></div><div><dt>Project</dt><dd>{receipt.project_id}</dd></div><div><dt>Caller-claimed source</dt><dd>{receipt.source} · not cryptographic agent proof</dd></div><div><dt>Observed / accepted</dt><dd>{formatDate(receipt.observed_on)} / {plainText(receipt.accepted_at)}</dd></div><div><dt>Submitted by</dt><dd>{plainText(receipt.submitted_by)}</dd></div><div><dt>Storage status</dt><dd>{receipt.persisted ? 'Server reports persisted' : 'Validated only — not stored'}</dd></div></dl>
      <TextList items={receipt.limitations} empty="No additional receipt limitations supplied." />
      <pre>{JSON.stringify(receiptExport(receipt, projectId).actual_progress, null, 2)}</pre>
      <p>{receipt.persisted ? 'Generation references only observation_id, without inline actual_progress or observed_on. The backend revalidates the current baseline before Risk.' : 'Explicit adoption uses the validated actual_progress unchanged, including genuine task update dates, in a dated inline report request. It is not a stored observation reference.'}</p>
      <div className="monitoring-actions">{receipt.persisted && !loadedByGet && <button type="button" disabled={!canRead || !storageConfigured || busy} onClick={() => review(receipt.observation_id)}>Load saved receipt by GET for review</button>}<button type="button" disabled={!canGenerate || receipt.persisted && (!canRead || !loadedByGet) || busy} onClick={adopt}>Use reviewed receipt for this report</button><button type="button" onClick={exportReceipt}>Export validated receipt JSON</button></div>
      <p className="monitoring-muted">The receipt is not used in generation until you explicitly adopt it. Export contains project observations and provenance; share privately.</p>
    </article>}
    {selectedReceipt && <Notice>{selectedReceipt.persisted ? 'Stored receipt selected for observation_id generation.' : 'Validation-only receipt explicitly adopted for dated inline generation.'} Editing manual rows or report dates clears this choice.</Notice>}
    <Notice error>{error}</Notice>
  </details>
}

function SourceWorkspace({ source, projectId, token, session, capabilities, onUnauthorized }) {
  const api = useMemo(() => createMonitoringApi({ token }), [token])
  const baselineOperation = useOperation(onUnauthorized)
  const historyOperation = useOperation(onUnauthorized)
  const detailOperation = useOperation(onUnauthorized)
  const generationOperation = useOperation(onUnauthorized)
  const [planningText, setPlanningText] = useState('')
  const [receivedOn, setReceivedOn] = useState('')
  const [importedBaseline, setImportedBaseline] = useState(null)
  const [rows, setRows] = useState([])
  const [asOf, setAsOf] = useState(localToday)
  const [observedOn, setObservedOn] = useState('')
  const [query, setQuery] = useState('')
  const [includeRisks, setIncludeRisks] = useState(true)
  const [saveReport, setSaveReport] = useState(false)
  const [validation, setValidation] = useState('')
  const [visible, setVisible] = useState(null)
  const [selectedReceipt, setSelectedReceipt] = useState(null)
  const [observationRevision, setObservationRevision] = useState(0)
  const canRead = session.permissions.includes('monitoring:read')
  const canGenerate = session.permissions.includes('monitoring:generate')
  const storageConfigured = capabilities?.storage_configured === true
  const baseline = source === 'live' ? baselineOperation.value : importedBaseline
  const baselineRun = baselineOperation.run
  const historyRun = historyOperation.run

  useEffect(() => {
    if (source === 'live' && canRead) {
      baselineRun(signal => api.baseline(projectId, signal)).then(data => {
        if (data) setRows(blankActualRows(data.tasks))
      })
    }
  }, [api, projectId, source, canRead, baselineRun])
  useEffect(() => {
    if (canRead && storageConfigured) historyRun(signal => api.history(projectId, signal))
  }, [api, projectId, canRead, storageConfigured, historyRun])

  function invalidate() {
    generationOperation.cancel()
    detailOperation.cancel()
    setVisible(null)
    setValidation('')
  }
  function clearReceipt() {
    setSelectedReceipt(null)
    setObservationRevision(previous => previous + 1)
  }
  function edit(setter, value) { invalidate(); setter(value) }
  function editActual(setter, value) { clearReceipt(); edit(setter, value) }
  function editImport(setter, value) {
    invalidate()
    clearReceipt()
    setter(value)
    setImportedBaseline(null)
    setRows([])
  }
  function applyImport() {
    invalidate()
    clearReceipt()
    try {
      const { baseline: parsed } = importedPlanning(planningText, projectId, receivedOn)
      if (receivedOn > asOf || receivedOn > localToday()) throw new Error('Planning receipt date cannot be after the report date or today.')
      setImportedBaseline(parsed)
      setRows(blankActualRows(parsed.tasks))
    } catch (error) { setValidation(error.message) }
  }
  function refreshBaseline() {
    if (!canRead) return
    invalidate()
    clearReceipt()
    setRows([])
    baselineRun(signal => api.baseline(projectId, signal)).then(data => { if (data) setRows(blankActualRows(data.tasks)) })
  }
  function updateRow(index, field, value) {
    invalidate()
    clearReceipt()
    setRows(previous => previous.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row))
  }
  async function generate(event) {
    event.preventDefault()
    invalidate()
    let body
    try {
      if (!canGenerate) throw new Error('The session does not include monitoring:generate. The server remains authoritative.')
      if (typeof crypto.randomUUID !== 'function') throw new Error('A secure browser context with crypto.randomUUID is required to create request IDs.')
      body = buildMonitoringRequest({ projectId, requestId: crypto.randomUUID(), asOf, baseline, planningText: source === 'import' ? planningText : '', receivedOn, rows: selectedReceipt ? [] : rows, observedOn, query, includeRisks, saveReport: saveReport && storageConfigured, storageConfigured })
      if (selectedReceipt) {
        validateObservationActual(selectedReceipt.actual_progress, projectId, selectedReceipt.observed_on, asOf)
        if (selectedReceipt.persisted) {
          if (!canRead) throw new Error('Stored observation generation requires both monitoring:read and monitoring:generate.')
          body.observation_id = selectedReceipt.observation_id
        } else {
          body.actual_progress = selectedReceipt.actual_progress
          body.observed_on = selectedReceipt.observed_on
        }
      }
    } catch (error) { setValidation(error.message); return }
    const report = await generationOperation.run(signal => api.generate(body, signal))
    if (report) {
      setVisible({ report, saved: body.save_report })
      if (body.save_report && canRead) historyRun(signal => api.history(projectId, signal))
    }
  }
  async function openReport(reportId) {
    invalidate()
    if (!canRead || !storageConfigured) return
    const report = await detailOperation.run(signal => api.report(projectId, reportId, signal))
    if (report) setVisible({ report, saved: true })
  }
  const includedCount = rows.filter(row => row.included).length
  const historicalLive = source === 'live' && baseline && asOf < baseline.fetched_on

  return <>
    <section className="monitoring-panel" aria-labelledby="monitoring-input-title">
      <div className="monitoring-section-heading"><div><p className="monitoring-eyebrow">01 / Verified inputs</p><h2 id="monitoring-input-title">Baseline & observations</h2></div>{source === 'live' && <button type="button" disabled={!canRead || baselineOperation.loading} onClick={refreshBaseline}>{baselineOperation.loading ? 'Loading baseline…' : 'Refresh baseline'}</button>}</div>
      <p className="monitoring-muted">Planning is a schedule baseline, not actual execution. Actual updates below are separate observations. Reports are cleared when their input selection changes.</p>
      {source === 'import' && <div className="monitoring-import"><label htmlFor="monitoring-planning-json">Real Planning SCHEDULE_CREATED envelope<textarea id="monitoring-planning-json" rows="8" value={planningText} onChange={event => editImport(setPlanningText, event.target.value)} spellCheck="false" aria-describedby="monitoring-import-help" /></label><p id="monitoring-import-help">Paste your real Planning message for the PDF/manual flow. No example data is filled in. It must belong to the authorized project; the server validates it again.</p><label>Message received on<input type="date" value={receivedOn} max={localToday()} onChange={event => editImport(setReceivedOn, event.target.value)} /></label><button type="button" onClick={applyImport} disabled={!planningText.trim() || !receivedOn}>Validate & use imported baseline</button></div>}
      <OperationNotice operation={baselineOperation} />
      {!baseline && !baselineOperation.loading && <p className="monitoring-empty">{source === 'live' ? 'No baseline loaded. Retry the live source or switch to an imported real Planning message.' : 'No imported baseline applied yet.'}</p>}
      {baseline && <div className="monitoring-baseline"><div className="monitoring-section-heading"><h3>{source === 'live' ? 'Current live baseline' : 'Imported baseline'}</h3><span>{baseline.tasks.length} supplied tasks</span></div><p>Project: {baseline.project_id} · Fetched / received: {formatDate(baseline.fetched_on)} · Source updated: {formatDate(baseline.updated_on)}</p><p className="monitoring-muted">This input baseline is not substituted into historical report details. No Planning status becomes an actual observation.</p><TextList items={baseline.limitations} empty="No baseline limitations supplied. Source freshness may still be unknown." /><details><summary>Inspect baseline schedule & raw Planning context</summary><div className="monitoring-table-scroll" tabIndex="0" role="region" aria-label="Baseline schedule, horizontally scrollable"><table><caption>Planning baseline only — not observed execution</caption><thead><tr><th scope="col">Task</th><th scope="col">Start</th><th scope="col">Due</th><th scope="col">Priority / critical</th><th scope="col">Dependencies</th><th scope="col">Supplied expected target</th></tr></thead><tbody>{baseline.tasks.map(task => <tr key={task.task_id}><th scope="row">{task.title}<small>{task.task_id}</small></th><td>{formatDate(task.start_date)}</td><td>{formatDate(task.due_date)}</td><td>{readable(task.priority)}{task.is_critical === true ? ' · critical' : ''}</td><td>{task.dependency_ids?.join(', ') || 'None supplied'}</td><td>{formatPercent(task.expected_progress)}</td></tr>)}</tbody></table></div><pre>{plainText(baseline.context)}</pre></details></div>}
      <ObservationIntake key={observationRevision} api={api} source={source} projectId={projectId} baseline={baseline} planningText={planningText} receivedOn={receivedOn} asOf={asOf} canRead={canRead} canGenerate={canGenerate} capabilities={capabilities} onUnauthorized={onUnauthorized} onAdopt={setSelectedReceipt} onInvalidate={invalidate} selectedReceipt={selectedReceipt} />
      <form onSubmit={generate} noValidate>
        <div className="monitoring-form-grid"><label>Report as of<input type="date" value={asOf} max={localToday()} required onChange={event => editActual(setAsOf, event.target.value)} /></label><label>Actuals recorded / observed on<input type="date" value={observedOn} max={asOf < localToday() ? asOf : localToday()} required={!selectedReceipt && includedCount > 0} onChange={event => editActual(setObservedOn, event.target.value)} aria-describedby="monitoring-actual-help" /></label></div>
        <Notice error>{historicalLive ? 'This live baseline was fetched after the report date. Historical reports require a real Planning message received on or before that date.' : ''}</Notice>
        {selectedReceipt && <Notice><strong>{selectedReceipt.persisted ? 'Using reviewed persisted receipt' : 'Using explicitly adopted validation-only receipt'}: {selectedReceipt.observation_id}</strong><p>Observed {formatDate(selectedReceipt.observed_on)} · source {selectedReceipt.source} (caller-claimed). Manual rows below are not used while this receipt is selected. Editing them or either report date clears the receipt; the backend validates current task IDs before Risk.</p><button type="button" onClick={() => { invalidate(); clearReceipt() }}>Clear receipt & use explicit manual rows</button></Notice>}
        <h3>Actual task observations</h3><p id="monitoring-actual-help" className="monitoring-muted">Include only tasks you actually observed. Every included row needs its own explicit status and progress. Blank or unchecked rows are missing observations, not 0%. Completed tasks require 100% and an end date.</p>
        {rows.length > 0 ? <div className="monitoring-table-scroll monitoring-observations" tabIndex="0" role="region" aria-label="Actual observations, horizontally scrollable"><table><caption>{includedCount} of {rows.length} tasks explicitly included</caption><thead><tr><th scope="col">Include</th><th scope="col">Task</th><th scope="col">Actual status</th><th scope="col">Progress %</th><th scope="col">Actual start</th><th scope="col">Actual end</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.task_id}><td><input type="checkbox" checked={row.included} disabled={!canGenerate} aria-label={`Include observation for ${row.task_id}`} onChange={event => updateRow(index, 'included', event.target.checked)} /></td><th scope="row">{baseline?.tasks[index]?.title}<small>{row.task_id}</small></th><td><select value={row.status} disabled={!row.included || !canGenerate} required={row.included} aria-label={`Actual status for ${row.task_id}`} onChange={event => updateRow(index, 'status', event.target.value)}><option value="">Choose observed status</option><option value="NOT_STARTED">Not started</option><option value="IN_PROGRESS">In progress</option><option value="BLOCKED">Blocked</option><option value="COMPLETED">Completed</option></select></td><td><input type="number" min="0" max="100" step="any" value={row.progress_percentage} disabled={!row.included || !canGenerate} required={row.included} aria-label={`Observed progress for ${row.task_id}`} onChange={event => updateRow(index, 'progress_percentage', event.target.value)} /></td><td><input type="date" value={row.actual_start} disabled={!row.included || !canGenerate} max={observedOn || asOf} aria-label={`Actual start for ${row.task_id}`} onChange={event => updateRow(index, 'actual_start', event.target.value)} /></td><td><input type="date" value={row.actual_end} disabled={!row.included || !canGenerate} max={observedOn || asOf} required={row.included && row.status === 'COMPLETED'} aria-label={`Actual end for ${row.task_id}`} onChange={event => updateRow(index, 'actual_end', event.target.value)} /></td></tr>)}</tbody></table></div> : <p className="monitoring-empty">No observation rows until a real baseline is loaded. A report can be generated without observations; actual progress will remain unknown.</p>}
        <details><summary>Optional report query</summary><label>Retrieval query<textarea rows="2" maxLength="500" value={query} onChange={event => edit(setQuery, event.target.value)} /></label><p className="monitoring-muted">Leave blank to use the server default. Do not enter secrets.</p></details>
        <div className="monitoring-options"><label><input type="checkbox" checked={includeRisks} onChange={event => edit(setIncludeRisks, event.target.checked)} /> Include risk source</label><label><input type="checkbox" checked={saveReport && storageConfigured} disabled={!storageConfigured || !canGenerate} onChange={event => edit(setSaveReport, event.target.checked)} /> Save report to configured storage (explicit opt-in)</label></div>
        {!storageConfigured && <p className="monitoring-muted">Storage is not confirmed configured. History and saving are unavailable; generation is still allowed.</p>}
        {includeRisks && capabilities?.risk_configured === false && <Notice>The risk source is not configured. Uncheck “Include risk source” for a partial report; otherwise the server will reject generation. This is not a zero-risk assessment.</Notice>}
        {!canGenerate && <Notice>This verified session has no monitoring:generate permission. Read access does not authorize generation.</Notice>}
        <Notice error>{validation}</Notice><OperationNotice operation={generationOperation} />
        <div className="monitoring-actions"><button className="monitoring-primary" type="submit" disabled={!canGenerate || !baseline || !!historicalLive || baselineOperation.loading || generationOperation.loading || detailOperation.loading}>{generationOperation.loading ? 'Generating report…' : 'Generate from available inputs'}</button>{generationOperation.loading && <button type="button" onClick={() => { invalidate(); setValidation('Request cancelled locally. If saving was requested, the server may still finish; check report history before retrying.') }}>Cancel request</button>}</div>
      </form>
    </section>
    <section className="monitoring-panel monitoring-no-print" aria-labelledby="monitoring-history-title"><div className="monitoring-section-heading"><div><p className="monitoring-eyebrow">02 / Saved artifacts</p><h2 id="monitoring-history-title">Report history</h2></div><button type="button" disabled={!canRead || !storageConfigured || historyOperation.loading} onClick={() => historyRun(signal => api.history(projectId, signal))}>{historyOperation.loading ? 'Loading history…' : 'Refresh history'}</button></div>
      {!canRead ? <p className="monitoring-empty">This session has no monitoring:read permission. Saved report access is disabled.</p> : !storageConfigured ? <p className="monitoring-empty">Report storage is not configured or not yet verified. You can still generate an unsaved report.</p> : <><OperationNotice operation={historyOperation} />{historyOperation.value?.length > 0 ? <ul className="monitoring-history">{historyOperation.value.map(report => <li key={report.report_id}><div><strong>{formatDate(report.as_of)} · {readable(report.status)}</strong><small>{report.report_id} · {reportScope(report)}</small></div><button type="button" disabled={detailOperation.loading || generationOperation.loading} onClick={() => openReport(report.report_id)}>View report<span className="monitoring-sr-only"> {report.report_id}</span></button></li>)}</ul> : !historyOperation.loading && !historyOperation.error && <p className="monitoring-empty">No saved reports returned for this authorized project.</p>}</>}
      <p className="monitoring-muted">History returns at most 20 saved reports for this authorized project; it is not an all-time record. Opening a saved report does not substitute the current baseline into its evidence.</p>
      {canRead && historyOperation.value?.length > 0 && <SavedTrends reports={historyOperation.value} projectId={projectId} />}
      {detailOperation.loading && <Notice>Loading saved report…</Notice>}<OperationNotice operation={detailOperation} />
    </section>
    {visible ? <ReportView key={`${visible.report.report_id}-${visible.report.request_id}`} report={visible.report} projectId={projectId} saved={visible.saved} reports={canRead ? historyOperation.value || [] : []} /> : <section className="monitoring-panel monitoring-empty" aria-live="polite"><h2>No report selected</h2><p>Generate a report from real inputs or open a saved report. No project metrics are fabricated while waiting.</p></section>}
  </>
}

function ProjectWorkspace(props) {
  const canRead = props.session.permissions.includes('monitoring:read')
  const [source, setSource] = useState(() => props.initialSource === 'import' || !canRead ? 'import' : 'live')
  return <><section className="monitoring-source"><label>Planning input source<select value={source} onChange={event => setSource(event.target.value)}><option value="live" disabled={!canRead}>Live Planning baseline (requires monitoring:read)</option><option value="import">Advanced: real Planning message / PDF flow</option></select></label><p>Changing source cancels local requests and clears the previous baseline, observations, and report. {!canRead && 'This session cannot read live baselines; use an authorized real Planning message for generation.'}</p></section><SourceWorkspace key={source} source={source} {...props} /></>
}


function ConnectionHelp() {
  return <aside className="monitoring-connection-help" aria-label="Connection troubleshooting">
    <p>Check your internet connection and try refreshing projects. If the problem continues, contact your administrator.</p>
  </aside>
}

export default function Monitoring({ session, onUnauthorized }) {
  const { projectId: routeProjectId } = useParams()
  const location = useLocation()
  const [attempt, setAttempt] = useState(0)
  const [initialSource, setInitialSource] = useState(undefined)
  function refreshAccess(source) {
    setInitialSource(source === 'import' ? 'import' : undefined)
    setAttempt(value => value + 1)
  }
  if (!session) return <Notice>Sign in to use Monitoring. <Link to="/login">Sign in</Link></Notice>
  // Remount before rendering a different account, URL, or refresh attempt. No
  // obsolete grant or private workspace survives while the new check is pending.
  return <AccountMonitoring key={`${monitoringAccountKey(session)}-${location.key}-${routeProjectId || ''}-${attempt}`} session={session} routeProjectId={routeProjectId} onUnauthorized={onUnauthorized} initialSource={initialSource} onRefresh={refreshAccess} />
}

function AccountMonitoring({ session, routeProjectId, onUnauthorized, initialSource, onRefresh }) {
  const navigate = useNavigate()
  const [accessFailure, setAccessFailure] = useState(null)
  const rejectAccess = useCallback(failure => {
    setAccessFailure(failure)
    if (failure.status === 401) onUnauthorized?.(session)
  }, [onUnauthorized, session])
  const health = useOperation()
  const connect = useOperation(rejectAccess)
  const healthRun = health.run
  const connectRun = connect.run
  useEffect(() => {
    // Shared styles constrain #root; release that constraint only for this route.
    const targets = [document.body, document.getElementById('root')].filter(Boolean)
    const added = targets.filter(target => !target.classList.contains('monitoring-route'))
    added.forEach(target => target.classList.add('monitoring-route'))
    return () => added.forEach(target => target.classList.remove('monitoring-route'))
  }, [])
  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (!active) return
      healthRun(signal => createMonitoringApi().health(signal))
      connectRun(signal => verifyAccountMonitoringSession(session, { signal }))
    })
    return () => { active = false }
  }, [session, healthRun, connectRun])
  const connection = accessFailure ? null : connect.value
  const projectIds = connection?.project_ids || []
  const selectedProject = routeProjectId && projectIds.includes(routeProjectId) ? routeProjectId : ''
  const capabilities = health.value?.capabilities
  const readiness = monitoringAccessReadiness(health.value)
  const healthGuidance = monitoringFailureGuidance(health.failure)
  const connectGuidance = monitoringFailureGuidance(accessFailure || connect.failure)

  const admin = session.role === 'admin'
  return <div className="monitoring-page">
    <a className="monitoring-skip" href="#monitoring-main">Skip to Monitoring content</a>
    <main id="monitoring-main" className="monitoring-main" tabIndex="-1">
      {routeProjectId && <nav className="monitoring-project-navigation" aria-label="Project navigation"><Link to="/monitoring">← Back to Monitoring projects</Link></nav>}
      <section className="monitoring-hero" aria-labelledby="monitoring-title"><div><p className="monitoring-eyebrow">Monitoring / Evidence-led reporting</p><h1 id="monitoring-title">Monitoring &amp; Reporting</h1><p>Compare a real plan with explicit task observations. Review progress, risks, and missing evidence without treating unknowns as completion or zero risk.</p></div></section>
      <section className="monitoring-panel monitoring-access" aria-labelledby="monitoring-access-title" aria-describedby="monitoring-access-help">
        <div className="monitoring-section-heading"><div><p className="monitoring-eyebrow">Start here / Signed-in access</p><h2 id="monitoring-access-title">Your Monitoring projects</h2><p id="monitoring-access-help" className="monitoring-muted">Signed in as <strong>{session.username}</strong> · {admin ? 'Administrator · all projects' : 'User · owned projects'}. Project access is verified with your current session before any project data is requested.</p></div><button type="button" onClick={onRefresh}>Refresh projects</button></div>

        <OperationNotice operation={health} />

        {readiness.authMissing && <Notice>{readiness.message}</Notice>}
        <p className="monitoring-muted">Refreshing projects clears current inputs and reports, including unsaved edits.</p>
        {connect.loading && <Notice>Verifying signed-in Monitoring access…</Notice>}
        <OperationNotice operation={connect} />
        {accessFailure && !connect.error && <Notice error><strong>{connectGuidance.title}</strong><p>{connectGuidance.message}</p><p>The previous project inputs and report have been cleared.</p></Notice>}
        {(connect.error || accessFailure) && <div className="monitoring-actions"><button type="button" onClick={onRefresh}>Retry Monitoring verification</button>{accessFailure?.status === 404 && !connect.error && <button type="button" onClick={() => onRefresh('import')}>Reverify access & use a real Planning message</button>}<Link to="/monitoring">Return to project chooser</Link></div>}
        {(healthGuidance?.showConnectionHelp || connectGuidance?.showConnectionHelp) && <ConnectionHelp />}
        {connection?.unsupported_project_count > 0 && <Notice>{connection.unsupported_project_count} accessible project(s) have IDs that Monitoring cannot use. Monitoring requires 1–100 ASCII letters, digits, dots, underscores or hyphens, starting with a letter or digit. These projects are unchanged and remain available in Schedule.</Notice>}
        {connection && <div className="monitoring-session"><label htmlFor="monitoring-project">{admin ? 'All projects' : 'Owned projects'}<select id="monitoring-project" value={selectedProject} onChange={event => navigate(event.target.value ? `/monitoring/${encodeURIComponent(event.target.value)}` : '/monitoring')}><option value="">Select an authorized project</option>{projectIds.map(id => <option key={id} value={id}>{id}</option>)}</select></label>{!projectIds.length && <Notice>No {admin ? 'accessible' : 'owned'} projects were returned for this signed-in session.</Notice>}{routeProjectId && !selectedProject && <Notice error>The project in this URL was not returned by your verified session. No project data was requested. Refresh access or choose an authorized project.</Notice>}</div>}
      </section>
      {connection && selectedProject ? <ProjectWorkspace key={selectedProject} projectId={selectedProject} token={session.access_token} session={connection} initialSource={initialSource} capabilities={capabilities} onUnauthorized={rejectAccess} /> : <section className="monitoring-panel monitoring-empty"><h2>{connection ? 'Choose your project' : 'Awaiting verified access'}</h2><p>{connection ? 'Only projects returned by the Monitoring server can be selected.' : 'Your current signed-in session must pass Monitoring verification. No project or metrics are prefilled.'}</p></section>}
    </main>
    <footer className="monitoring-footer"><p>APM / Monitoring & reporting · Decisions remain with your team.</p></footer>
  </div>
}
