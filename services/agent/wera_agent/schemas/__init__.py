"""WERA's Pydantic contracts. `EXPORTED` is what packages/schemas/json and the web app's
generated TypeScript types are built from (SCH-03)."""

from .api import (
    ApiErrorBody,
    ApplicationList,
    ApplicationPatchIn,
    CvFileSaved,
    CvImportIn,
    CvVersion,
    DecisionIn,
    DocumentPatchIn,
    ImportTextIn,
    ImportUrlIn,
    JobList,
    JobsCleared,
    NavCounts,
    PlaceOption,
    PreviewRead,
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
from .cv_file import CvFile, CvFileIn, CvPreview
from .cv_profile import CvProfile
from .jobs import FitEvidence, FitReport, FitWeights, Job, ScoredJob
from .learning import SuggestionList
from .pack import AnswerItem, AtsCompare, AtsReport, CoverLetter, JobAnalysis, Pack, TailoredCV, WritingReport
from .profile import SavedProfile, SearchProfile
from .runs import ChannelSummary, Estimate, IntakeRun, RunIds
from .settings import Settings, SettingsPatch, SettingsResponse
from .strategy import TargetMatrix
from .upskills import UpskillsReport, UpskillsVersion

# Responses describe what the web app receives; request bodies what it sends.
RESPONSES = [
    MasterCV,
    UpskillsReport,
    UpskillsVersion,
    SuggestionList,
    Contact,
    CvLine,
    CvVersion,
    CvProfile,
    TargetMatrix,
    SearchProfile,
    SavedProfile,
    ProfileProposalResult,
    Job,
    ScoredJob,
    FitEvidence,
    FitReport,
    FitWeights,
    TailoredCV,
    CoverLetter,
    AtsReport,
    AtsCompare,
    WritingReport,
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
    PlaceOption,
    SiteOption,
    JobList,
    ApplicationList,
    TestResult,
    JobsCleared,
    PreviewRead,
    CvFile,
    CvPreview,
    CvFileSaved,
    ApiErrorBody,
]
REQUESTS = [
    DecisionIn,
    ApplicationPatchIn,
    DocumentPatchIn,
    ImportUrlIn,
    ImportTextIn,
    RegenerateIn,
    CvFileIn,
    CvImportIn,
    RunIn,
    SiteReviewIn,
    SettingsPatch,
]
