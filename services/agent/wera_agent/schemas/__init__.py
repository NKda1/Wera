"""WERA's Pydantic contracts. `EXPORTED` is what packages/schemas/json and the web app's
generated TypeScript types are built from (SCH-03)."""

from .api import (
    ApiErrorBody,
    ApplicationList,
    ApplicationPatchIn,
    CvImportIn,
    CvVersion,
    DecisionIn,
    DocumentPatchIn,
    ImportTextIn,
    ImportUrlIn,
    JobList,
    JobsCleared,
    NavCounts,
    ProfileProposalResult,
    RegenerateIn,
    RunIn,
    ShortlistMeta,
    SiteOption,
    SiteReviewIn,
    TestResult,
)
from .applications import Application, ApplicationListItem, ApplicationWithJob
from .cv import Contact, CvLine, MasterCV
from .cv_profile import CvProfile
from .jobs import FitEvidence, FitReport, FitWeights, Job, ScoredJob
from .pack import AnswerItem, AtsReport, CoverLetter, JobAnalysis, Pack, TailoredCV
from .profile import SearchProfile
from .runs import ChannelSummary, Estimate, IntakeRun, RunIds
from .settings import Settings, SettingsPatch, SettingsResponse
from .strategy import TargetMatrix

# Responses describe what the web app receives; request bodies what it sends.
RESPONSES = [
    MasterCV,
    Contact,
    CvLine,
    CvVersion,
    CvProfile,
    TargetMatrix,
    SearchProfile,
    ProfileProposalResult,
    Job,
    ScoredJob,
    FitEvidence,
    FitReport,
    FitWeights,
    TailoredCV,
    CoverLetter,
    AtsReport,
    AnswerItem,
    JobAnalysis,
    Pack,
    Application,
    ApplicationWithJob,
    ApplicationListItem,
    IntakeRun,
    ChannelSummary,
    Estimate,
    RunIds,
    Settings,
    SettingsResponse,
    NavCounts,
    ShortlistMeta,
    SiteOption,
    JobList,
    ApplicationList,
    TestResult,
    JobsCleared,
    ApiErrorBody,
]
REQUESTS = [
    DecisionIn,
    ApplicationPatchIn,
    DocumentPatchIn,
    ImportUrlIn,
    ImportTextIn,
    RegenerateIn,
    CvImportIn,
    RunIn,
    SiteReviewIn,
    SettingsPatch,
]
