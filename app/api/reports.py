"""
Reports API Endpoints

REST API endpoints for generating and downloading detailed simulation reports:
- POST /reports - Start report generation
- GET /reports/{report_id}/status - Check generation progress
- GET /reports/{report_id}/download - Download completed PDF
- DELETE /reports/{report_id} - Delete a report (optional)

Reports are generated asynchronously in the background.
Frontend polls the status endpoint until generation completes.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, status
from fastapi.responses import Response

from app.models.report import (
    ReportRequest,
    ReportResponse,
    ReportStatusResponse,
    ReportStatus,
    ReportOptions,
)
from app.services.report_generator import ReportGeneratorService
from app.dependencies import get_mongo_client
from app.core.mongo_client import MongoClient


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


# =============================================================================
# Dependency: Get Report Generator Service
# =============================================================================

async def get_report_generator(
    mongo_client: MongoClient = Depends(get_mongo_client)
) -> ReportGeneratorService:
    """
    Dependency to get the ReportGeneratorService instance.
    
    Args:
        mongo_client: MongoDB client from dependency injection
        
    Returns:
        ReportGeneratorService instance
    """
    db = mongo_client.db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection not available"
        )
    return ReportGeneratorService(db)


# =============================================================================
# POST /reports - Start Report Generation
# =============================================================================

@router.post(
    "",
    response_model=ReportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start report generation",
    description="""
    Initiates generation of a detailed simulation report.
    
    The request returns immediately with a report_id.
    Report generation happens asynchronously in the background.
    
    Use the status endpoint to poll for completion.
    """,
    responses={
        202: {"description": "Report generation started"},
        400: {"description": "Invalid request (missing run_id or pfd_image)"},
        404: {"description": "Simulation run not found"},
    }
)
async def create_report(
    request: ReportRequest,
    background_tasks: BackgroundTasks,
    report_generator: ReportGeneratorService = Depends(get_report_generator),
) -> ReportResponse:
    """
    Start report generation for a simulation run.
    
    Args:
        request: ReportRequest with run_id, pfd_image_base64, and options
        background_tasks: FastAPI background tasks handler
        report_generator: ReportGeneratorService instance
        
    Returns:
        ReportResponse with report_id and initial status
    """
    logger.info(f"Starting report generation for run_id: {request.run_id}")
    
    # Validate PFD image is provided
    if not request.pfd_image_base64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PFD image is required (pfd_image_base64)"
        )
    
    # Create report record and get response
    response = await report_generator.generate_report(
        run_id=request.run_id,
        pfd_image_base64=request.pfd_image_base64,
        options=request.options,
    )
    
    # Schedule background processing
    background_tasks.add_task(
        report_generator._process_report,
        report_id=response.report_id,
        run_id=request.run_id,
        pfd_image_base64=request.pfd_image_base64,
        options=request.options or ReportOptions(),
    )
    
    logger.info(f"Report generation scheduled: {response.report_id}")
    
    return response


# =============================================================================
# GET /reports/{report_id}/status - Check Progress
# =============================================================================

@router.get(
    "/{report_id}/status",
    response_model=ReportStatusResponse,
    summary="Get report generation status",
    description="""
    Check the current status of a report generation.
    
    Poll this endpoint to track progress:
    - status: pending/processing/completed/failed
    - progress: 0-100%
    - current_step: Human-readable description of current step
    - download_url: Available when status is 'completed'
    """,
    responses={
        200: {"description": "Report status returned"},
        404: {"description": "Report not found"},
    }
)
async def get_report_status(
    report_id: str,
    report_generator: ReportGeneratorService = Depends(get_report_generator),
) -> ReportStatusResponse:
    """
    Get the current status of a report.
    
    Args:
        report_id: Unique report identifier
        report_generator: ReportGeneratorService instance
        
    Returns:
        ReportStatusResponse with current progress
    """
    result = await report_generator.get_report_status(report_id)
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report not found: {report_id}"
        )
    
    return result


# =============================================================================
# GET /reports/{report_id}/download - Download PDF
# =============================================================================

@router.get(
    "/{report_id}/download",
    summary="Download completed report",
    description="""
    Download the generated PDF report.
    
    Returns the PDF file with appropriate headers for download.
    Only available when report status is 'completed'.
    """,
    responses={
        200: {
            "description": "PDF file",
            "content": {"application/pdf": {}}
        },
        404: {"description": "Report not found"},
        400: {"description": "Report not ready (still processing or failed)"},
    }
)
async def download_report(
    report_id: str,
    report_generator: ReportGeneratorService = Depends(get_report_generator),
) -> Response:
    """
    Download the generated PDF report.
    
    Args:
        report_id: Unique report identifier
        report_generator: ReportGeneratorService instance
        
    Returns:
        PDF file as Response with download headers
    """
    # Check status first
    status_result = await report_generator.get_report_status(report_id)
    
    if not status_result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report not found: {report_id}"
        )
    
    if status_result.status == ReportStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Report generation failed: {status_result.error}"
        )
    
    if status_result.status != ReportStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Report not ready. Status: {status_result.status.value}, Progress: {status_result.progress}%"
        )
    
    # Get the PDF file
    result = await report_generator.get_report_file(report_id)
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report file not found on server"
        )
    
    pdf_bytes, filename = result
    
    logger.info(f"Serving report download: {filename} ({len(pdf_bytes)} bytes)")
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        }
    )


# =============================================================================
# DELETE /reports/{report_id} - Delete Report (Optional)
# =============================================================================

@router.delete(
    "/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a report",
    description="""
    Delete a report and its associated PDF file.
    
    Use this to clean up reports that are no longer needed.
    """,
    responses={
        204: {"description": "Report deleted successfully"},
        404: {"description": "Report not found"},
    }
)
async def delete_report(
    report_id: str,
    report_generator: ReportGeneratorService = Depends(get_report_generator),
):
    """
    Delete a report and its PDF file.
    
    Args:
        report_id: Unique report identifier
        report_generator: ReportGeneratorService instance
    """
    success = await report_generator.delete_report(report_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report not found: {report_id}"
        )
    
    logger.info(f"Report deleted: {report_id}")
    
    # Return 204 No Content (no body needed)
    return None
