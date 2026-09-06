from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MeasurementState = Literal["measured", "partial", "not_measured", "no_usage"]
MeasurementReason = Literal[
    "complete",
    "no_usage",
    "population_without_usage",
    "historical_unavailable",
    "current_data_gap",
    "mixed_history_and_current_gap",
    "mixed_no_usage_and_data_gap",
]


class AnalyticsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DataFreshness(AnalyticsModel):
    state: Literal["fresh", "stale", "unknown"]
    dataThrough: str


class RateMeasurement(AnalyticsModel):
    value: float | None
    measuredCount: int
    totalCount: int
    measurementState: MeasurementState
    measurementReason: MeasurementReason


class LatencyMeasurement(AnalyticsModel):
    valueMs: int | None
    measuredCount: int
    totalCount: int
    measurementState: MeasurementState
    measurementReason: MeasurementReason


class MeasurementCoverage(AnalyticsModel):
    measuredCount: int
    totalCount: int
    measurementState: MeasurementState
    measurementReason: MeasurementReason


class AnalyticsAxisQuality(MeasurementCoverage):
    isolatedCount: int


class SourcePipelineQuality(AnalyticsModel):
    publishedRunId: str
    latestRunId: str
    latestRunStatus: str
    latestRunErrorCode: str
    latestRunFinishedAt: str
    diagnosticsStatus: Literal["available", "unavailable"] = "available"
    diagnosticsErrorCode: str = ""
    state: Literal["clean", "degraded", "blocked", "unknown", "unavailable"]
    quarantinedEventCount: int
    deduplicatedDeliveryCount: int
    repairedDuplicateFactCount: int
    axisUnmeasuredFindingCount: int
    batchBlockingFailureCount: int


class AnalyticsQuality(AnalyticsModel):
    contractVersion: Literal["dashboard_events_v2"]
    isolatedEventCount: int
    totalEventCount: int
    classification: AnalyticsAxisQuality
    task: AnalyticsAxisQuality
    product: AnalyticsAxisQuality
    sourcePipeline: SourcePipelineQuality


class ContentDiagnostics(AnalyticsModel):
    state: Literal["complete", "degraded"]
    labelCatalogStatus: Literal[
        "available", "partial", "unavailable", "not_applicable"
    ]
    rosterStatus: Literal["available", "partial"]
    rosterIsolatedCount: int = Field(ge=0)
    rosterIssueCounts: dict[str, int]
    issues: list[str]


class Kpis(AnalyticsModel):
    activeUsers: int
    adoptionRate: float | None
    returnRate: float | None
    questionsPerActiveUser: float | None
    completeDelivery: RateMeasurement
    p95Latency: LatencyMeasurement


class HourlyQuestion(AnalyticsModel):
    hour: str
    count: int


class DistributionRow(AnalyticsModel):
    key: str
    count: int
    rate: float | None
    label: str | None = None


class UsageTrendRow(AnalyticsModel):
    date: str
    activeUsers: int
    questions: int
    isPartial: bool


class ActivitySegment(AnalyticsModel):
    key: Literal["high", "middle", "low", "dormant"]
    label: str
    count: int
    rate: float | None


class ActivityStack(AnalyticsModel):
    label: str
    total: int
    segments: list[ActivitySegment]


class CountRow(AnalyticsModel):
    label: str
    count: int


class ProductTaskCell(AnalyticsModel):
    product: str
    task: str
    taskLabel: str
    count: int


class ProductResolution(AnalyticsModel):
    candidateCount: int
    resolvedCount: int
    unresolvedQuestions: int
    resolutionRate: float | None
    measuredCount: int
    totalCount: int
    measurementState: MeasurementState
    measurementReason: MeasurementReason


class AnalyticsLabel(AnalyticsModel):
    labelId: str
    name: str
    color: str


class RegionRow(AnalyticsModel):
    areaKey: str
    area: str
    rosterUsers: int
    activeUsers: int
    questions: int
    adoptionRate: float | None
    returnRate: float | None


class UserRow(AnalyticsModel):
    rosterId: str
    name: str
    email: str
    area: str
    areaKey: str
    workplace: str
    role: str
    department: str
    labels: list[AnalyticsLabel]
    lastActiveAt: str
    activeDays7: int
    userMessageCount7: int
    activeDaysInPeriod: int = Field(ge=0)
    userMessageCountInPeriod: int = Field(ge=0)
    completeDelivery: RateMeasurement
    activity: Literal["high", "middle", "low", "dormant"] | None
    activityLabel: str


class UserProfile(AnalyticsModel):
    rosterId: str
    name: str
    email: str
    area: str
    workplace: str
    role: str
    department: str
    mrExperience: str
    labels: list[AnalyticsLabel]


class UserSummary(AnalyticsModel):
    lastActiveAt: str
    activeDays: int
    questions: int
    questionsPerActiveDay: float | None
    completeDelivery: RateMeasurement
    p95Latency: LatencyMeasurement


class PeerComparison(AnalyticsModel):
    label: str
    peerCount: int
    averageQuestions: float | None
    averageActiveDays: float | None
    averageCompleteDelivery: RateMeasurement


class UserComparisons(AnalyticsModel):
    area: PeerComparison
    role: PeerComparison


class UserTrendRow(AnalyticsModel):
    date: str
    questions: int
    completeDelivery: RateMeasurement
    isPartial: bool


class ConversationRow(AnalyticsModel):
    conversationId: str
    title: str
    messageCount: int
    updatedAt: str
    updatedAtJst: str


class UsagePanelResponse(AnalyticsModel):
    scope: Literal["global"]
    scopePolicyVersion: str
    rosterFingerprint: str
    contentFingerprint: str
    publishedRunId: str
    windowStart: str
    windowEnd: str
    windowTimezone: str
    scopeUserCount: int
    freshness: DataFreshness


class EnvironmentResponse(UsagePanelResponse):
    hourlyQuestions: list[HourlyQuestion]
    deviceDistribution: list[DistributionRow]
    deviceMeasurement: MeasurementCoverage
    modeDistribution: list[DistributionRow]
    modeMeasurement: MeasurementCoverage


class TrendResponse(UsagePanelResponse):
    usageTrend: list[UsageTrendRow]
    requestTasks: list[DistributionRow]
    taskMeasurement: MeasurementCoverage


class OverviewResponse(AnalyticsModel):
    scope: Literal["global"]
    scopePolicyVersion: str
    rosterFingerprint: str
    contentFingerprint: str
    publishedRunId: str
    windowStart: str
    windowEnd: str
    windowTimezone: str
    contentDiagnostics: ContentDiagnostics
    scopeUserCount: int
    freshness: DataFreshness
    analyticsQuality: AnalyticsQuality
    kpis: Kpis
    hourlyQuestions: list[HourlyQuestion]
    deviceDistribution: list[DistributionRow]
    deviceMeasurement: MeasurementCoverage
    modeDistribution: list[DistributionRow]
    modeMeasurement: MeasurementCoverage
    usageTrend: list[UsageTrendRow]
    requestTasks: list[DistributionRow]
    taskMeasurement: MeasurementCoverage
    activityDistribution: list[ActivitySegment]
    activityByArea: list[ActivityStack]
    activityByRole: list[ActivityStack]
    topProducts: list[CountRow]
    productTaskMatrix: list[ProductTaskCell]
    productResolution: ProductResolution


class RegionsResponse(AnalyticsModel):
    scope: Literal["global"]
    scopePolicyVersion: str
    rosterFingerprint: str
    contentFingerprint: str
    publishedRunId: str
    windowStart: str
    windowEnd: str
    windowTimezone: str
    contentDiagnostics: ContentDiagnostics
    scopeUserCount: int
    freshness: DataFreshness
    regions: list[RegionRow]


class UsersResponse(AnalyticsModel):
    scope: Literal["global", "user_map"]
    scopePolicyVersion: str
    rosterFingerprint: str
    contentFingerprint: str
    publishedRunId: str
    windowStart: str
    windowEnd: str
    windowTimezone: str
    contentDiagnostics: ContentDiagnostics
    scopeUserCount: int
    freshness: DataFreshness
    users: list[UserRow]


class UserDetailResponse(AnalyticsModel):
    scope: Literal["user_map"]
    scopePolicyVersion: str
    rosterFingerprint: str
    contentFingerprint: str
    publishedRunId: str
    windowStart: str
    windowEnd: str
    windowTimezone: str
    contentDiagnostics: ContentDiagnostics
    freshness: DataFreshness
    analyticsQuality: AnalyticsQuality
    profile: UserProfile
    summary: UserSummary
    comparisons: UserComparisons
    trend: list[UserTrendRow]
    products: list[CountRow]
    productResolution: ProductResolution
    tasks: list[DistributionRow]
    taskMeasurement: MeasurementCoverage
    questionCategories: list[DistributionRow]
    questionCategoryMeasurement: MeasurementCoverage
    modes: list[DistributionRow]
    modeMeasurement: MeasurementCoverage
    devices: list[DistributionRow]
    deviceMeasurement: MeasurementCoverage


class ConversationsResponse(AnalyticsModel):
    status: Literal["ready", "identity_unmatched"]
    conversations: list[ConversationRow]
