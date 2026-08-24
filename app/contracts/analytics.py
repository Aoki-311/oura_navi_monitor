from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class AnalyticsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Kpis(AnalyticsModel):
    activeUsers: int
    adoptionRate: float | None
    returnRate: float | None
    questionsPerActiveUser: float | None
    completeDeliveryRate: float | None
    p95LatencyMs: int | None


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


class ProductCategoryCell(AnalyticsModel):
    product: str
    category: str
    count: int


class ProductResolution(AnalyticsModel):
    candidateCount: int
    resolvedCount: int
    unresolvedQuestions: int
    resolutionRate: float | None


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
    questionCount7: int
    completeDeliveryRate: float | None
    activity: Literal["high", "middle", "low", "dormant"]
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
    completeDeliveryRate: float | None


class PeerComparison(AnalyticsModel):
    label: str
    peerCount: int
    averageQuestions: float | None
    averageActiveDays: float | None
    averageCompleteDeliveryRate: float | None


class UserComparisons(AnalyticsModel):
    area: PeerComparison
    role: PeerComparison


class UserTrendRow(AnalyticsModel):
    date: str
    questions: int
    completeDeliveryRate: float | None


class ConversationRow(AnalyticsModel):
    conversationId: str
    title: str
    messageCount: int
    updatedAt: str
    updatedAtJst: str


class OverviewResponse(AnalyticsModel):
    scope: Literal["global"]
    status: Literal["ready", "unavailable"]
    dataThrough: str
    kpis: Kpis
    hourlyQuestions: list[HourlyQuestion]
    deviceDistribution: list[DistributionRow]
    modeDistribution: list[DistributionRow]
    usageTrend: list[UsageTrendRow]
    questionCategories: list[DistributionRow]
    activityDistribution: list[ActivitySegment]
    activityByArea: list[ActivityStack]
    activityByRole: list[ActivityStack]
    topProducts: list[CountRow]
    productQuestionMatrix: list[ProductCategoryCell]
    productResolution: ProductResolution


class RegionsResponse(AnalyticsModel):
    status: Literal["ready", "unavailable"]
    dataThrough: str
    regions: list[RegionRow]


class UsersResponse(AnalyticsModel):
    status: Literal["ready", "unavailable"]
    dataThrough: str
    users: list[UserRow]


class UserDetailResponse(AnalyticsModel):
    status: Literal["ready", "unavailable"]
    dataThrough: str
    profile: UserProfile
    summary: UserSummary
    comparisons: UserComparisons
    trend: list[UserTrendRow]
    products: list[CountRow]
    productResolution: ProductResolution
    tasks: list[DistributionRow]
    questionCategories: list[DistributionRow]
    modes: list[DistributionRow]
    devices: list[DistributionRow]
    conversations: list[ConversationRow]
