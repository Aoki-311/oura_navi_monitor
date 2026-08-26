from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

MeasurementState = Literal["measured", "partial", "not_measured", "no_usage"]


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


class LatencyMeasurement(AnalyticsModel):
    valueMs: int | None
    measuredCount: int
    totalCount: int
    measurementState: MeasurementState


class MeasurementCoverage(AnalyticsModel):
    measuredCount: int
    totalCount: int
    measurementState: MeasurementState


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
    labels: list[AnalyticsLabel]
    lastActiveAt: str
    activeDays7: int
    userMessageCount7: int
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


class ConversationRow(AnalyticsModel):
    conversationId: str
    title: str
    messageCount: int
    updatedAt: str
    updatedAtJst: str


class OverviewResponse(AnalyticsModel):
    scope: Literal["global"]
    scopeUserCount: int
    freshness: DataFreshness
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
    scopeUserCount: int
    freshness: DataFreshness
    regions: list[RegionRow]


class UsersResponse(AnalyticsModel):
    scopeUserCount: int
    freshness: DataFreshness
    users: list[UserRow]


class UserDetailResponse(AnalyticsModel):
    freshness: DataFreshness
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
