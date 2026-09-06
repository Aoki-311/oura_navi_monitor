from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NewsUsageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NewsUsageReportState(NewsUsageModel):
    availability: Literal[
        "available", "before_measurement", "not_enabled", "unavailable"
    ]
    usage: Literal["has_usage", "no_usage", "not_measured"]
    freshness: Literal["fresh", "stale", "unknown"]
    historyCoverage: Literal["full", "partial", "none"]
    publicationCoverage: Literal["full", "partial", "none"]
    reasonCode: str
    message: str
    measurementStartAt: str
    dataThrough: str
    publishedAt: str


class NewsUsageDiagnostics(NewsUsageModel):
    state: Literal["available", "unavailable", "not_applicable"]
    unmatchedEventCount: int = Field(ge=0)
    errorCode: str


class NewsUsageOption(NewsUsageModel):
    value: str
    label: str


class NewsUsageFilterOptions(NewsUsageModel):
    channels: list[NewsUsageOption]
    environments: list[NewsUsageOption]
    businessUnits: list[NewsUsageOption]
    geographies: list[NewsUsageOption]
    categories: list[NewsUsageOption]
    societies: list[NewsUsageOption]


class NewsUsageSelection(NewsUsageModel):
    channel: str
    environment: str
    businessUnit: str
    geography: str
    category: str
    society: str
    query: str


class NewsUsageKpis(NewsUsageModel):
    scopeUsers: int = Field(ge=0)
    activeUsers: int = Field(ge=0)
    adoptionRate: float | None
    totalActions: int = Field(ge=0)
    tabViews: int = Field(ge=0)
    filterChanges: int = Field(ge=0)
    detailViews: int = Field(ge=0)
    outboundClicks: int = Field(ge=0)
    exportStarts: int = Field(ge=0)
    manualSummaryViews: int = Field(ge=0)


class NewsUsageCountRow(NewsUsageModel):
    key: str
    label: str
    actions: int = Field(ge=0)
    activeUsers: int = Field(ge=0)


class NewsUsageTrendRow(NewsUsageModel):
    date: str
    activeUsers: int = Field(ge=0)
    tabViews: int = Field(ge=0)
    filterChanges: int = Field(ge=0)
    detailViews: int = Field(ge=0)
    outboundClicks: int = Field(ge=0)
    exportStarts: int = Field(ge=0)
    manualSummaryViews: int = Field(ge=0)
    isPartial: bool


class NewsUsageTabBehavior(NewsUsageModel):
    views: int = Field(ge=0)
    activeUsers: int = Field(ge=0)
    byChannel: list[NewsUsageCountRow]


class NewsUsageFilterBehavior(NewsUsageModel):
    changes: int = Field(ge=0)
    activeUsers: int = Field(ge=0)
    searchChanges: int = Field(ge=0)
    searchEnabledAfterChange: int = Field(ge=0)
    byChangedField: list[NewsUsageCountRow]


class NewsUsageArticleRow(NewsUsageModel):
    contentEventId: str
    contentEventVersion: str
    channel: Literal["news", "society"]
    businessUnit: str
    geography: str
    sourceId: str
    category: str
    detailViews: int = Field(ge=0)
    outboundClicks: int = Field(ge=0)
    activeUsers: int = Field(ge=0)


class NewsUsageDetailBehavior(NewsUsageModel):
    views: int = Field(ge=0)
    activeUsers: int = Field(ge=0)
    totalArticles: int = Field(ge=0)
    isTruncated: bool
    popularArticles: list[NewsUsageArticleRow]


class NewsUsageOutboundBehavior(NewsUsageModel):
    clicks: int = Field(ge=0)
    activeUsers: int = Field(ge=0)
    totalArticles: int = Field(ge=0)
    isTruncated: bool
    byLinkKind: list[NewsUsageCountRow]
    popularArticles: list[NewsUsageArticleRow]


class NewsUsageExportResultRow(NewsUsageModel):
    result: str
    attempts: int = Field(ge=0)


class NewsUsageExportBehavior(NewsUsageModel):
    started: int = Field(ge=0)
    activeUsers: int = Field(ge=0)
    finished: int = Field(ge=0)
    pending: int = Field(ge=0)
    orphanFinished: int = Field(ge=0)
    downloadHandoffRate: float | None
    results: list[NewsUsageExportResultRow]


class NewsUsageSummaryBehavior(NewsUsageModel):
    manualViews: int = Field(ge=0)
    manualUsers: int = Field(ge=0)
    automaticViews: int = Field(ge=0)
    automaticUsers: int = Field(ge=0)


class NewsUsageUserRow(NewsUsageModel):
    rosterId: str
    name: str
    area: str
    areaKey: str
    workplace: str
    role: str
    department: str
    actions: int = Field(ge=0)
    activeDays: int = Field(ge=0)
    lastActiveAt: str


class NewsUsageOrganizationRow(NewsUsageModel):
    key: str
    label: str
    scopeUsers: int = Field(ge=0)
    activeUsers: int = Field(ge=0)
    actions: int = Field(ge=0)
    adoptionRate: float | None


class NewsUsageOrganizations(NewsUsageModel):
    users: list[NewsUsageUserRow]
    departments: list[NewsUsageOrganizationRow]
    regions: list[NewsUsageOrganizationRow]


class NewsUsageReportResponse(NewsUsageModel):
    contractVersion: Literal["news_usage_report_v1"]
    scope: Literal["global"]
    scopePolicyVersion: str
    rosterFingerprint: str
    contentFingerprint: str
    publishedRunId: str
    rosterSnapshotRunId: str
    sourceService: str
    windowStart: str
    windowEnd: str
    windowTimezone: Literal["Asia/Tokyo"]
    state: NewsUsageReportState
    diagnostics: NewsUsageDiagnostics
    selection: NewsUsageSelection
    filterOptions: NewsUsageFilterOptions
    kpis: NewsUsageKpis
    trend: list[NewsUsageTrendRow]
    tabBehavior: NewsUsageTabBehavior
    filterBehavior: NewsUsageFilterBehavior
    detailBehavior: NewsUsageDetailBehavior
    outboundBehavior: NewsUsageOutboundBehavior
    exportBehavior: NewsUsageExportBehavior
    summaryBehavior: NewsUsageSummaryBehavior
    organizations: NewsUsageOrganizations


class NewsUsageDashboardTotals(NewsUsageModel):
    tabViews: int = Field(ge=0)
    newsTabViews: int = Field(ge=0)
    societyTabViews: int = Field(ge=0)
    contentClicks: int = Field(ge=0)
    newsContentClicks: int = Field(ge=0)
    societyContentClicks: int = Field(ge=0)
    newsDomesticClicks: int = Field(ge=0)
    newsOverseasClicks: int = Field(ge=0)
    newsUnknownGeographyClicks: int = Field(ge=0)


class NewsUsageDashboardDay(NewsUsageModel):
    date: str
    tabViews: int = Field(ge=0)
    newsTabViews: int = Field(ge=0)
    societyTabViews: int = Field(ge=0)
    contentClicks: int = Field(ge=0)
    newsContentClicks: int = Field(ge=0)
    societyContentClicks: int = Field(ge=0)


class NewsUsageCategoryClicks(NewsUsageModel):
    key: str
    label: str
    clicks: int = Field(ge=0)
    domesticClicks: int = Field(ge=0)
    overseasClicks: int = Field(ge=0)
    unknownGeographyClicks: int = Field(ge=0)


class NewsUsageSourceClicks(NewsUsageModel):
    key: str
    label: str
    clicks: int = Field(ge=0)


class NewsUsageSocietyCategoryClicks(NewsUsageModel):
    key: str
    label: str
    clicks: int = Field(ge=0)
    sources: list[NewsUsageSourceClicks]


class NewsUsageDashboardResponse(NewsUsageModel):
    contractVersion: Literal["news_usage_dashboard_v1"]
    scope: Literal["global", "user_map"]
    rosterId: str
    windowStart: str
    windowEnd: str
    state: NewsUsageReportState
    publishedRunId: str
    rosterFingerprint: str
    totals: NewsUsageDashboardTotals | None
    trend: list[NewsUsageDashboardDay]
    newsCategories: list[NewsUsageCategoryClicks]
    societyCategories: list[NewsUsageSocietyCategoryClicks]
